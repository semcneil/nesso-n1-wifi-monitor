import sys
import json
import collections
import serial
import serial.tools.list_ports
from PyQt5 import QtWidgets, QtCore
import pyqtgraph as pg

class SerialThread(QtCore.QThread):
    data_received = QtCore.pyqtSignal(dict)

    def __init__(self, port, baud=115200):
        super().__init__()
        self.port = port
        self.baud = baud
        self.running = True
        self.ser = None

    def run(self):
        try:
            self.ser = serial.Serial(self.port, self.baud, timeout=1)
            
            # Toggle DTR to reset ESP32 on connection (forces boot message)
            self.ser.setDTR(False)
            QtCore.QThread.msleep(100)
            self.ser.setDTR(True)

            while self.running:
                line = self.ser.readline().decode('utf-8', errors='ignore').strip()
                if line.startswith('{') and line.endswith('}'):
                    try:
                        data = json.loads(line)
                        self.data_received.emit(data)
                    except json.JSONDecodeError:
                        pass
            self.ser.close()
        except Exception as e:
            print(f"Serial Error: {e}")

    def send_command(self, cmd_str):
        if self.ser and self.ser.is_open:
            self.ser.write((cmd_str + "\n").encode('utf-8'))

    def stop(self):
        self.running = False
        self.wait()

class WiFiVisualizer(QtWidgets.QMainWindow):
    def __init__(self, serial_port):
        super().__init__()
        self.setWindowTitle("Wi-Fi Signal Radar — ESP32-C6 Auto-Syncing GUI")
        self.resize(1100, 650)

        self.history_len = 100
        self.rssi_history = collections.deque(maxlen=self.history_len)
        self.is_synced = False  

        central_widget = QtWidgets.QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QtWidgets.QVBoxLayout(central_widget)

        control_panel = QtWidgets.QHBoxLayout()
        
        control_panel.addWidget(QtWidgets.QLabel("Channel:"))
        self.channel_box = QtWidgets.QComboBox()
        for ch in range(1, 14):
            self.channel_box.addItem(f"Channel {ch}", ch)
        self.channel_box.currentIndexChanged.connect(self.user_changed_channel)
        control_panel.addWidget(self.channel_box)

        control_panel.addWidget(QtWidgets.QLabel("Target MAC:"))
        self.mac_input = QtWidgets.QLineEdit()
        self.mac_input.setPlaceholderText("e.g. AA:BB:CC:DD:EE:FF")
        control_panel.addWidget(self.mac_input)
        
        self.btn_set_mac = QtWidgets.QPushButton("Set MAC")
        self.btn_set_mac.clicked.connect(self.user_set_mac)
        control_panel.addWidget(self.btn_set_mac)

        self.btn_clear_filter = QtWidgets.QPushButton("Clear Filter")
        self.btn_clear_filter.clicked.connect(self.user_clear_filter)
        control_panel.addWidget(self.btn_clear_filter)

        self.status_label = QtWidgets.QLabel("Status: Connecting & Syncing...")
        control_panel.addWidget(self.status_label)
        
        main_layout.addLayout(control_panel)

        self.rssi_plot = pg.PlotWidget(title="Live Target RSSI Stream (dBm)")
        self.rssi_plot.setYRange(-100, -20)
        self.rssi_plot.setLabel('left', 'RSSI', units='dBm')
        self.rssi_curve = self.rssi_plot.plot(pen=pg.mkPen('g', width=2))
        main_layout.addWidget(self.rssi_plot)

        # Start Serial Thread
        self.thread = SerialThread(serial_port)
        self.thread.data_received.connect(self.process_packet)
        self.thread.start()

        # Fallback timer: Request state if no boot packet received within 1.5s
        QtCore.QTimer.singleShot(1500, self.request_status_ping)

    def request_status_ping(self):
        if not self.is_synced:
            self.thread.send_command("GET_STATUS")

    @QtCore.pyqtSlot(dict)
    def process_packet(self, data):
        # 1. Handle Boot or Requested Status Payload
        if data.get("boot") is True or data.get("status_resp") is True:
            self.sync_gui_from_boot(data)
            return

        # 2. If receiving normal telemetry before sync, unlock GUI with defaults
        if 'rssi' in data and not self.is_synced:
            self.is_synced = True
            self.status_label.setText("Status: Connected (Streaming)")

        if 'rssi' in data:
            self.rssi_history.append(data['rssi'])
            self.rssi_curve.setData(list(self.rssi_history))

    def sync_gui_from_boot(self, boot_data):
        self.is_synced = False  

        saved_chan = boot_data.get("chan", 6)
        index = self.channel_box.findData(saved_chan)
        if index != -1:
            self.channel_box.setCurrentIndex(index)

        saved_mac = boot_data.get("mac", "00:00:00:00:00:00")
        if saved_mac != "00:00:00:00:00:00":
            self.mac_input.setText(saved_mac)

        filter_active = boot_data.get("filter", "false") == "true" or boot_data.get("filter") is True
        if filter_active:
            self.status_label.setText(f"Synced: Ch {saved_chan} | Filtering {saved_mac}")
        else:
            self.status_label.setText(f"Synced: Ch {saved_chan} | Capturing All MACs")

        self.is_synced = True

    def user_changed_channel(self):
        if not self.is_synced:
            return
        selected_chan = self.channel_box.currentData()
        self.thread.send_command(f"SET_CHAN:{selected_chan}")
        self.status_label.setText(f"Status: Switched to Channel {selected_chan}")
        self.rssi_history.clear()

    def user_set_mac(self):
        mac_text = self.mac_input.text().strip().upper()
        if len(mac_text) == 17 and mac_text.count(':') == 5:
            self.thread.send_command(f"SET_MAC:{mac_text}")
            self.status_label.setText(f"Status: Filtering MAC ({mac_text})")
            self.rssi_history.clear()
        else:
            self.status_label.setText("Error: Invalid MAC format!")

    def user_clear_filter(self):
        self.thread.send_command("DISABLE_FILTER")
        self.status_label.setText("Status: Capturing All MACs")

    def closeEvent(self, event):
        self.thread.stop()
        event.accept()

def main():
    app = QtWidgets.QApplication(sys.argv)
    ports = [p.device for p in serial.tools.list_ports.comports()]
    if not ports:
        print("No serial ports found! Connect your Nesso N1.")
        return

    print("Available ports:", ports)
    try:
        port = [x for x in ports if "usbmodem" in x][0]
    except IndexError as e:
        print("No usbmodem found, quitting")
        return
    selected_port = port  # Default to first available port
    print(f"Connecting to {selected_port}...") 

    win = WiFiVisualizer(selected_port)
    win.show()
    sys.exit(app.exec_())

if __name__ == "__main__":
    main()