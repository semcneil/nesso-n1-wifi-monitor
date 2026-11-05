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
        self.setWindowTitle("Wi-Fi Signal Radar — ESP32-C6 Dynamic Filter")
        self.resize(1100, 650)

        self.history_len = 100
        self.rssi_history = collections.deque(maxlen=self.history_len)

        # Central widget and main vertical layout
        central_widget = QtWidgets.QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QtWidgets.QVBoxLayout(central_widget)

        # Control Panel (Top Bar)
        control_panel = QtWidgets.QHBoxLayout()
        
        self.mac_input = QtWidgets.QLineEdit()
        self.mac_input.setPlaceholderText("e.g. AA:BB:CC:DD:EE:FF")
        
        self.btn_set_mac = QtWidgets.QPushButton("Set Target MAC")
        self.btn_set_mac.clicked.connect(self.update_target_mac)

        self.btn_clear_filter = QtWidgets.QPushButton("Show All Traffic")
        self.btn_clear_filter.clicked.connect(self.clear_filter)

        self.status_label = QtWidgets.QLabel("Filter: OFF (Capturing All)")

        control_panel.addWidget(QtWidgets.QLabel("Target MAC:"))
        control_panel.addWidget(self.mac_input)
        control_panel.addWidget(self.btn_set_mac)
        control_panel.addWidget(self.btn_clear_filter)
        control_panel.addWidget(self.status_label)
        
        main_layout.addLayout(control_panel)

        # Signal Plot
        self.rssi_plot = pg.PlotWidget(title="Live Target RSSI Stream (dBm)")
        self.rssi_plot.setYRange(-100, -20)
        self.rssi_plot.setLabel('left', 'RSSI', units='dBm')
        self.rssi_curve = self.rssi_plot.plot(pen=pg.mkPen('g', width=2))
        main_layout.addWidget(self.rssi_plot)

        # Start Serial Thread
        self.thread = SerialThread(serial_port)
        self.thread.data_received.connect(self.process_packet)
        self.thread.start()

    def update_target_mac(self):
        mac_text = self.mac_input.text().strip().upper()
        if len(mac_text) == 17 and mac_text.count(':') == 5:
            self.thread.send_command(f"SET_MAC:{mac_text}")
            self.status_label.setText(f"Filter: ON ({mac_text})")
            self.rssi_history.clear()
        else:
            self.status_label.setText("Error: Invalid MAC format!")

    def clear_filter(self):
        self.thread.send_command("DISABLE_FILTER")
        self.status_label.setText("Filter: OFF (Capturing All)")

    @QtCore.pyqtSlot(dict)
    def process_packet(self, data):
        if 'rssi' in data:
            self.rssi_history.append(data['rssi'])
            self.rssi_curve.setData(list(self.rssi_history))

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

    win = WiFiVisualizer(selected_port)
    win.show()
    sys.exit(app.exec_())

if __name__ == "__main__":
    main()