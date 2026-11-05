import sys
import json
import time
import collections
import serial
import serial.tools.list_ports
from PyQt5 import QtWidgets, QtCore
import pyqtgraph as pg
import pdb

# Serial Reader Thread to keep the GUI responsive
class SerialThread(QtCore.QThread):
    data_received = QtCore.pyqtSignal(dict)

    def __init__(self, port, baud=115200):
        super().__init__()
        self.port = port
        self.baud = baud
        self.running = True

    def run(self):
        try:
            ser = serial.Serial(self.port, self.baud, timeout=1)
            while self.running:
                line = ser.readline().decode('utf-8', errors='ignore').strip()
                if line.startswith('{') and line.endswith('}'):
                    try:
                        data = json.loads(line)
                        self.data_received.emit(data)
                    except json.JSONDecodeError:
                        pass
            ser.close()
        except Exception as e:
            print(f"Serial Error: {e}")

    def stop(self):
        self.running = False
        self.wait()

# Main GUI Window
class WiFiVisualizer(QtWidgets.QMainWindow):
    def __init__(self, serial_port):
        super().__init__()
        self.setWindowTitle("Wi-Fi Signal Radar — ESP32 Nesso N1")
        self.resize(1000, 600)

        # Data Structures
        self.history_len = 100
        self.rssi_history = collections.deque(maxlen=self.history_len)
        self.channel_counts = collections.defaultdict(int)

        # Layout Setup
        central_widget = QtWidgets.QWidget()
        self.setCentralWidget(central_widget)
        layout = QtWidgets.QHBoxLayout(central_widget)

        # Plot 1: RSSI Signal Stream
        self.rssi_plot = pg.PlotWidget(title="Live RSSI Stream (dBm)")
        self.rssi_plot.setYRange(-100, -20)
        self.rssi_plot.setLabel('left', 'RSSI', units='dBm')
        self.rssi_plot.setLabel('bottom', 'Captured Packets')
        self.rssi_curve = self.rssi_plot.plot(pen=pg.mkPen('g', width=2))
        layout.addWidget(self.rssi_plot, stretch=2)

        # Plot 2: Channel Activity Histogram
        self.channel_plot = pg.PlotWidget(title="Channel Activity (Packets / Channel)")
        self.channel_plot.setYRange(0, 50)
        self.channel_plot.setLabel('bottom', 'Wi-Fi Channel')
        self.channel_bar = pg.BarGraphItem(x=list(range(1, 14)), height=[0]*13, width=0.6, brush='b')
        self.channel_plot.addItem(self.channel_bar)
        layout.addWidget(self.channel_plot, stretch=1)

        # Start Serial Thread
        self.thread = SerialThread(serial_port)
        self.thread.data_received.connect(self.process_packet)
        self.thread.start()

    @QtCore.pyqtSlot(dict)
    def process_packet(self, data):
        is_ax = data.get('is_wifi6', False)
        # protocol_str = "802.11ax (Wi-Fi 6)" if is_ax else "802.11b/g/n"
            
        # Print or trigger specific UI elements when a Wi-Fi 6 frame arrives
        if is_ax:
            print(f"[Wi-Fi 6 Frame Captured] MAC: {data['mac']} | Channel: {data['chan']}")        # Record RSSI
        self.rssi_history.append(data['rssi'])
        self.rssi_curve.setData(list(self.rssi_history))

        # Record Channel Activity
        chan = data['chan']
        if 1 <= chan <= 13:
            self.channel_counts[chan] += 1
            heights = [self.channel_counts[c] for c in range(1, 14)]
            self.channel_bar.setOpts(height=heights)

    def closeEvent(self, event):
        self.thread.stop()
        event.accept()

# Port Auto-Detection & Launch
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