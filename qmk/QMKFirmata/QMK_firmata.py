import os, sys, time, hid, argparse, json
import cv2, numpy as np
import asyncio, websockets

from PySide6 import QtCore
from PySide6.QtCore import Qt, QThread, Signal, QTimer
from PySide6.QtWidgets import QApplication, QMainWindow, QTabWidget, QWidget, QVBoxLayout, QHBoxLayout, QFrame
from PySide6.QtWidgets import QTextEdit, QPushButton, QFileDialog, QLabel, QSlider, QLineEdit, QTreeView
from PySide6.QtWidgets import QCheckBox, QComboBox, QMessageBox, QStyledItemDelegate
from PySide6.QtGui import QImage, QPixmap, QColor, QFont, QTextCursor, QFontMetrics, QMouseEvent, QKeyEvent, QKeySequence
from PySide6.QtGui import QIntValidator, QDoubleValidator
from PySide6.QtGui import QStandardItemModel, QStandardItem

try:
    import pyaudiowpatch as pyaudio
    from WinFocusListener import WinFocusListener
except:
    pass

from FirmataKeyboard import FirmataKeyboard
from DebugTracer import DebugTracer

if __name__ != "__main__":
    exit()

#-------------------------------------------------------------------------------
class WSServer(QThread):

    def __init__(self, msg_handler, port = 8765):
        self.dbg = DebugTracer(zones={'D':1}, obj=self)

        self.port = port
        self.loop = None
        self.msg_handler = msg_handler
        super().__init__()

    def run(self):
        self.dbg.tr('D', f"ws server start on port: {self.port}")
        asyncio.run(self.ws_main())

    async def ws_main(self):
        self.loop = asyncio.get_running_loop()
        self.stop_ev = self.loop.create_future()
        async with websockets.serve(self.msg_handler, "localhost", self.port):
            await self.stop_ev
        self.dbg.tr('D', "ws server stopped")

    async def ws_close(self):
        # dummy connect to exit ws_main
        try:
            async with websockets.connect(f"ws://localhost:{self.port}") as websocket:
                await websocket.send("")
        except Exception as e:
            pass

    def stop(self):
        #self.dbg.tr('D', "ws server stop")
        if self.loop:
            self.stop_ev.set_result(None)
            asyncio.run(self.ws_close())

#-------------------------------------------------------------------------------
class ConsoleTab(QWidget): # todo: move to separate file
    signal_cli_command = Signal(str)

    class CommandLineEdit(QLineEdit):
        def __init__(self, parent=None):
            super().__init__(parent)
            self.command_history = []
            self.history_index = 0

        def keyPressEvent(self, event):
            if event.key() == Qt.Key.Key_Up:
                if self.history_index > 0:
                    self.history_index -= 1
                    self.setText(self.command_history[self.history_index])
                    self.selectAll()
                return
            elif event.key() == Qt.Key.Key_Down:
                if self.history_index < len(self.command_history) - 1:
                    self.history_index += 1
                    self.setText(self.command_history[self.history_index])
                elif self.history_index == len(self.command_history) - 1:
                    self.history_index += 1
                    self.clear()
                return
            super().keyPressEvent(event)

        def store_clear_command(self):
            command = self.text().strip()
            if command:
                self.command_history.append(command)
                self.history_index = len(self.command_history)
                self.clear()

    def __init__(self, keyboard_model):
        self.dbg = DebugTracer(zones={'D':0}, obj=self)

        self.keyboard_model = keyboard_model
        try:
            self.keyboard_config = self.keyboard_model.keyb_config()
        except:
            self.keyboard_config = None
        self.dbg.tr('DEBUG', f"keyboard_model: {self.keyboard_model} {self.keyboard_config}")

        self.max_text_length = 2000000
        self.max_console_file_size = self.max_text_length*2
        self.console_text_len = 0
        self.console_file_clear = True

        super().__init__()
        self.init_gui()

    def handle_cli_command(self):
        cmd = self.cli.text()
        self.dbg.tr('DEBUG', f"cli command: {cmd}")
        self.signal_cli_command.emit(cmd)
        self.cli.store_clear_command()

    def init_gui(self):
        # console output
        layout = QVBoxLayout()
        self.cli = self.CommandLineEdit()
        self.console_output = QTextEdit()
        self.console_output.setReadOnly(True)
        font = QFont()
        font.setFamily("Courier New")
        self.cli.setFont(font)
        self.console_output.setFont(font)
        self.cli.returnPressed.connect(self.handle_cli_command)
        layout.addWidget(self.cli)
        layout.addWidget(self.console_output)
        self.setLayout(layout)

    def update_text(self, text):
        cursor = self.console_output.textCursor()
        cursor.movePosition(QTextCursor.End)
        self.console_output.setTextCursor(cursor)
        self.console_output.insertPlainText(text)
        self.console_output.ensureCursorVisible()
        self.console_text_len += len(text)
        self.limit_text()

    def save_to_file(self, filename, clear=False):
        # check file size and append
        try:
            filesize = os.path.getsize(filename)
            if clear:
                try:
                    os.remove(filename)
                    filesize = 0
                except Exception as e:
                    self.dbg.tr('DEBUG', f"remove console log: {e}")
        except:
            filesize = 0
        if filesize < self.max_console_file_size:
            try:
                with open(filename, "a") as file:
                    file.write(self.console_output.toPlainText())
            except Exception as e:
                self.dbg.tr('DEBUG', f"save_to_file: {e}")

    def limit_text(self):
        if self.console_text_len >= self.max_text_length:
            self.save_to_file("console_output.txt", self.console_file_clear)
            self.console_file_clear = False
            self.console_output.clear()
            self.console_text_len = 0

#-------------------------------------------------------------------------------
class RGBMatrixTab(QWidget): # todo: move to separate file
    def __init__(self, keyboard_model):
        self.keyboard_model = keyboard_model
        try:
            self.keyboard_config = self.keyboard_model.keyb_config()
        except:
            self.keyboard_config = None
        self.rgb_matrix_size = keyboard_model.rgb_matrix_size()
        super().__init__()
        self.init_gui()

    def init_gui(self):
        layout = QVBoxLayout()
        self.tab_widget = QTabWidget()

        self.rgb_video_tab = RGBVideoTab(self, self.rgb_matrix_size)
        self.rgb_animation_tab = RGBAnimationTab(self.rgb_matrix_size)
        self.rgb_audio_tab = RGBAudioTab(self.rgb_matrix_size)
        self.rgb_dynld_animation_tab = RGBDynLDAnimationTab()

        self.tab_widget.addTab(self.rgb_video_tab, 'video')
        self.tab_widget.addTab(self.rgb_animation_tab, 'animation')
        self.tab_widget.addTab(self.rgb_audio_tab, 'audio')
        self.tab_widget.addTab(self.rgb_dynld_animation_tab, 'dynld animation')

        layout.addWidget(self.tab_widget)
        self.setLayout(layout)

#-------------------------------------------------------------------------------
class RGBVideoTab(QWidget):
    signal_rgb_image = Signal(QImage, object)

    def __init__(self, rgb_matrix_tab, rgb_matrix_size):
        self.dbg = DebugTracer(zones={'D':1, 'WS_MSG':0}, obj=self)
        super().__init__()

        self.rgb_matrix_tab = rgb_matrix_tab
        self.cap = None
        self.framerate = 25
        self.rgb_matrix_size = rgb_matrix_size
        self.rgb_multiplier = (1.0,1.0,1.0)
        self.init_gui()

    async def ws_handler(self, websocket, path):
        try:
            async for message in websocket:
                try:
                    message = bytearray(message, 'utf-8')
                except:
                    pass # already bytearray
                self.dbg.tr('WS_MSG', f"ws_handler: {message}")
                sub = b"rgb."
                if message.startswith(sub):
                    message = message[len(sub):]
                    subs = [ b"mode:", b"img:" ]
                    for sub in subs:
                        if message.startswith(sub):
                            if sub == b"img:":
                                data = message[len(sub):]
                                w = self.rgb_matrix_size[0]
                                h = self.rgb_matrix_size[1]
                                img = QImage(w, h, QImage.Format_RGB888)
                                img.fill(QColor('black'))
                                for y in range(h):
                                    for x in range(w):
                                        index = (y * w + x) * 3
                                        try:
                                            r, g, b = data[index], data[index + 1], data[index + 2]
                                            img.setPixelColor(x, y, QColor(r, g, b))
                                        except Exception as e:
                                            #self.dbg.tr('WS_MSG', f"ws_handler: {e}")
                                            pass

                                self.signal_rgb_image.emit(img, self.rgb_multiplier)
                                self.dbg.tr('WS_MSG', f"ws_handler:emit(img) done")
                self.dbg.tr('WS_MSG', f"ws_handler: message handled")
                await asyncio.sleep(0)  # Ensures control is yielded back to the event loop

        except Exception as e:
            self.dbg.tr('WS_MSG', f"ws_handler: {e}")
        self.dbg.tr('WS_MSG', f"ws_handler: done")
        self.signal_rgb_image.emit(None, self.rgb_multiplier)

    def ws_server_startstop(self, state):
        #self.dbg.tr('DEBUG', f"{state}")
        if Qt.CheckState(state) == Qt.CheckState.Checked:
            self.ws_server = WSServer(self.ws_handler, int(self.ws_server_port.text()))
            self.ws_server.start()
        else:
            try:
                self.ws_server.stop()
                self.ws_server.wait()
                self.ws_server = None
            except Exception as e:
                self.dbg.tr('DEBUG', f"{e}")

    def init_gui(self):
        layout = QVBoxLayout()
        self.video_label = QLabel("")
        self.video_label.setFixedSize(app_width, app_height)
        self.video_label.setAlignment(Qt.AlignTop)

        hlayout = QHBoxLayout()
        self.open_button = QPushButton("open file")
        self.open_button.setFixedWidth(100)
        self.open_button.clicked.connect(self.open_file)
        #---------------------------------------
        #region "rgb video ws server" enable checkbox plus port input
        self.ws_server_checkbox = QCheckBox("enable ws server", self)
        self.ws_server_port = QLineEdit("8787")
        port_validator = QIntValidator(0, 65535, self)
        self.ws_server_port.setValidator(port_validator)
        self.ws_server_port.setFixedWidth(50)
        self.ws_server_checkbox.stateChanged.connect(self.ws_server_startstop)
        hlayout = QHBoxLayout()
        hlayout.addStretch(1)
        hlayout.addWidget(self.ws_server_checkbox)
        hlayout.addWidget(self.ws_server_port)
        #endregion

        controls_layout = QHBoxLayout()
        self.framerate_label = QLabel("frame rate")
        self.framerate_slider = QSlider(Qt.Horizontal)
        self.framerate_slider.setMinimum(1)  # Minimum framerate
        self.framerate_slider.setMaximum(120)  # Maximum framerate
        self.framerate_slider.setValue(self.framerate)  # Set the default value
        self.framerate_slider.setTickInterval(1)  # Set tick interval
        self.framerate_slider.setTickPosition(QSlider.TicksBelow)
        self.framerate_slider.setToolTip("frame rate")
        self.framerate_slider.valueChanged.connect(self.adjust_framerate)

        #region framerate/RGB multiplier sliders
        rgb_multiply_layout = QHBoxLayout()
        self.rgb_r_label = QLabel("r")
        self.rgb_r_slider = QSlider(QtCore.Qt.Horizontal)
        self.rgb_r_slider.setMinimum(0)
        self.rgb_r_slider.setMaximum(300)
        self.rgb_r_slider.setValue(int(self.rgb_multiplier[0]*100))
        self.rgb_r_slider.setTickInterval(10)
        self.rgb_r_slider.setTickPosition(QSlider.TicksBelow)
        self.rgb_r_slider.setToolTip("red multiplier")
        self.rgb_r_slider.valueChanged.connect(self.adjust_rgb_multiplier)
        self.rgb_g_label = QLabel("g")
        self.rgb_g_slider = QSlider(Qt.Horizontal)
        self.rgb_g_slider.setMinimum(0)
        self.rgb_g_slider.setMaximum(300)
        self.rgb_g_slider.setValue(int(self.rgb_multiplier[1]*100))
        self.rgb_g_slider.setTickInterval(10)
        self.rgb_g_slider.setTickPosition(QSlider.TicksBelow)
        self.rgb_g_slider.setToolTip("green multiplier")
        self.rgb_g_slider.valueChanged.connect(self.adjust_rgb_multiplier)
        self.rgb_b_label = QLabel("b")
        self.rgb_b_slider = QSlider(Qt.Horizontal)
        self.rgb_b_slider.setMinimum(0)
        self.rgb_b_slider.setMaximum(300)
        self.rgb_b_slider.setValue(int(self.rgb_multiplier[2]*100))
        self.rgb_b_slider.setTickInterval(10)
        self.rgb_b_slider.setTickPosition(QSlider.TicksBelow)
        self.rgb_b_slider.setToolTip("blue multiplier")
        self.rgb_b_slider.valueChanged.connect(self.adjust_rgb_multiplier)

        controls_layout.addWidget(self.framerate_label)
        controls_layout.addWidget(self.framerate_slider)
        rgb_multiply_layout.addWidget(self.rgb_r_label)
        rgb_multiply_layout.addWidget(self.rgb_r_slider)
        rgb_multiply_layout.addWidget(self.rgb_g_label)
        rgb_multiply_layout.addWidget(self.rgb_g_slider)
        rgb_multiply_layout.addWidget(self.rgb_b_label)
        rgb_multiply_layout.addWidget(self.rgb_b_slider)
        #endregion

        layout.addLayout(hlayout)
        layout.addWidget(self.video_label)
        layout.addWidget(self.open_button)
        layout.addLayout(controls_layout)
        layout.addLayout(rgb_multiply_layout)
        self.setLayout(layout)

        self.timer = QTimer()
        self.timer.timeout.connect(self.display_video_frame)

    def adjust_framerate(self, value):
        self.framerate = value
        if self.cap is not None and self.cap.isOpened():
            self.timer.start(1000 / self.framerate)

    def adjust_rgb_multiplier(self, value):
        if self.sender() == self.rgb_r_slider:
            self.rgb_multiplier = (value/100, self.rgb_multiplier[1], self.rgb_multiplier[2])
        if self.sender() == self.rgb_g_slider:
            self.rgb_multiplier = (self.rgb_multiplier[0], value/100, self.rgb_multiplier[2])
        if self.sender() == self.rgb_b_slider:
            self.rgb_multiplier = (self.rgb_multiplier[0], self.rgb_multiplier[1], value/100)
        #print(self.RGB_multiplier)

    def open_file(self):
        if self.cap is not None and self.cap.isOpened():
            self.cap.release()
            self.timer.stop()
            self.signal_rgb_image.emit(None, self.rgb_multiplier)
            self.open_button.setText("open file")
            return

        filename, _ = QFileDialog.getOpenFileName(self, "open file", "", "Video Files (*.mp4 *.avi *.mov *.webm *.gif)")
        if filename:
            self.cap = cv2.VideoCapture(filename)
            fps = self.cap.get(cv2.CAP_PROP_FPS)  # Get the video's frame rate
            self.framerate = fps if fps > 0 else 25
            self.framerate_slider.setValue(int(self.framerate))
            self.timer.start(1000 / self.framerate)
            self.open_button.setText("stop")

    def display_video_frame(self):
        ret, frame = self.cap.read()
        if ret:
            rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            h, w, ch = rgb_frame.shape
            bytes_per_line = ch * w
            rgb_img = QImage(rgb_frame.data, w, h, bytes_per_line, QImage.Format_RGB888)
            scaled_img = rgb_img.scaled(app_width, app_height, aspectMode=QtCore.Qt.AspectRatioMode.KeepAspectRatio)
            self.video_label.setPixmap(QPixmap.fromImage(scaled_img))

            keyb_rgb = scaled_img.scaled(self.rgb_matrix_size[0], self.rgb_matrix_size[1])
            self.signal_rgb_image.emit(keyb_rgb, self.rgb_multiplier)
        else:
            self.cap.set(cv2.CAP_PROP_POS_FRAMES, 0)  # restart

    def print_rgb_data(self, frame):
        print(frame[0,0])

    def closeEvent(self, event):
        if self.cap is not None and self.cap.isOpened():
            self.cap.release()
        self.timer.stop()

#-------------------------------------------------------------------------------
class AudioCaptureThread(QThread): # todo: move to separate file

    def __init__(self, freq_bands, interval):
        self.dbg = DebugTracer(zones={'D':0}, obj=self)

        super().__init__()
        self.running = False
        self.freq_bands = freq_bands
        self.interval = interval
        self.paudio = pyaudio.PyAudio()

    def connect_callback(self, callback):
        self.callback = callback

    def set_freq_bands(self, freq_bands):
        self.freq_bands = freq_bands

    def run(self):
        dbg_zone = 'DEBUG'

        self.paudio = pyaudio.PyAudio()
        default_speakers = None
        try:
            # see https://github.com/s0d3s/PyAudioWPatch/blob/master/examples/pawp_record_wasapi_loopback.py
            wasapi_info = self.paudio.get_host_api_info_by_type(pyaudio.paWASAPI)
            self.dbg.tr(dbg_zone, f"wasapi: {wasapi_info}")

            default_speakers = self.paudio.get_device_info_by_index(wasapi_info["defaultOutputDevice"])
            if not default_speakers["isLoopbackDevice"]:
                for loopback in self.paudio.get_loopback_device_info_generator():
                    if default_speakers["name"] in loopback["name"]:
                        default_speakers = loopback
                        break

            self.dbg.tr(dbg_zone, f"loopback device: {default_speakers}")
        except Exception as e:
            self.dbg.tr(dbg_zone, f"wasapi not supported: {e}")
            return

        FORMAT = pyaudio.paFloat32
        CHANNELS = default_speakers["maxInputChannels"]
        RATE = int(default_speakers["defaultSampleRate"])
        INPUT_INDEX = default_speakers["index"]
        CHUNK = int(RATE * self.interval)

        self.running = True
        self.stream = self.paudio.open(format=FORMAT,
                        channels=CHANNELS,
                        rate=RATE,
                        input=True,
                        frames_per_buffer=CHUNK,
                        input_device_index=INPUT_INDEX)
        self.dbg.tr(dbg_zone, f"audio stream {self.stream} opened")

        while self.running:
            frames = []
            try:
                data = self.stream.read(CHUNK)
                frames = np.frombuffer(data, dtype=np.float32)
            except Exception as e:
                self.dbg.tr(dbg_zone, f"audio stream read error: {e}")
                self.running = False
                break

            audio_data = np.hstack(frames)
            freq_data = np.fft.rfft(audio_data)
            freq_magnitude = np.abs(freq_data)

            # Calculate frequency bins
            freq_bins = np.fft.rfftfreq(len(audio_data), d=1./RATE)
            peak_levels = []
            for f_min, f_max in self.freq_bands:
                # Find the bin indices corresponding to the frequency range
                idx = np.where((freq_bins >= f_min) & (freq_bins <= f_max))
                if len(freq_magnitude[idx]) > 0:
                    peak_level = np.max(freq_magnitude[idx])
                    peak_levels.append(peak_level)

            self.callback(peak_levels)

        self.stream.stop_stream()
        self.stream.close()
        self.dbg.tr(dbg_zone, f"audio stream {self.stream} closed")
        self.paudio.terminate()
        self.callback(None)

    def stop(self):
        try:
            self.stream.stop_stream()
        except Exception as e:
            pass
        self.running = False

#-------------------------------------------------------------------------------
class RGBAudioTab(QWidget):
    signal_rgb_image = Signal(QImage, object)

    @staticmethod
    def freq_bands_linear(f_min, f_max, k):
        bands = []
        step = (f_max - f_min)/k
        for i in range(k):
            bands.append((f_min + i*step, f_min + (i+1)*step))
        return bands

    @staticmethod
    def freq_bands_log(f_min, f_max, k):
        #f_min = 27.5  # Hz (frequency of A0)
        #f_max = 4186  # Hz (frequency of C8)
        #k = 16        # number of bands
        # Calculate the frequency boundaries for each of the 16 bands
        i = np.arange(k + 1)  # index array from 0 to k (inclusive)
        bounds = f_min * (f_max / f_min) ** (i / k)
        # Prepare the bands in a readable format
        bands = [(bounds[n], bounds[n + 1]) for n in range(k)]
        return bands

    @staticmethod
    def freq_bands_for(f_min, f_max, k):
        return RGBAudioTab.freq_bands_log(f_min, f_max, k)

    def __init__(self, rgb_matrix_size):
        #-----------------------------------------------------------
        self.dbg = DebugTracer(zones={'D':0, "FREQ_BAND":0, "PEAK_LEVEL":0, "MAX_PEAK":0}, obj=self)
        #-----------------------------------------------------------
        self.freq_bands = []
        super().__init__()
        self.init_gui()

        #-----------------------------------------------------------
        self.rgb_matrix_size = rgb_matrix_size
        self.keyb_rgb = QImage(self.rgb_matrix_size[0], self.rgb_matrix_size[1], QImage.Format_RGB888)
        self.keyb_rgb_mask = QImage(self.keyb_rgb.size(), QImage.Format_Grayscale8)
        self.keyb_rgb_mask_mode = 0
        self.rgb_multiplier = (1.0,1.0,1.0)

        self.sample_count = 0
        try:
            self.audio_thread = AudioCaptureThread(self.freq_bands, 0.05)
        except:
            self.audio_thread = None

    def load_freqbands_jsonfile(self):
        filename, _ = QFileDialog.getOpenFileName(self, "open file", "", "json (*.json)")
        self.load_freq_bands_colors(filename)
        # update freq bands rgb ui
        for i, (band, color) in enumerate(zip(self.freq_bands, self.freq_rgb)):
            self.dbg.tr('FREQ_BAND', f"settext:[{i}]{band} {color}")
            self.freqbands_input[i][0].blockSignals(True)
            self.freqbands_input[i][1].blockSignals(True)
            self.freqbands_input[i][0].setText(format(band[0], '.2f'))
            self.freqbands_input[i][1].setText(format(band[1], '.2f'))
            self.freqbands_input[i][0].blockSignals(False)
            self.freqbands_input[i][1].blockSignals(False)

            for j in range(3):
                self.freqbands_rgb_input[i][j].blockSignals(True)
                self.freqbands_rgb_input[i][j].setText(format(color[j], '.2f'))
                self.freqbands_rgb_input[i][j].blockSignals(False)

            self.minmax_level_input[i][0].blockSignals(True)
            self.minmax_level_input[i][1].blockSignals(True)
            self.minmax_level_input[i][0].setText(str(self.min_max_level[i][0]))
            self.minmax_level_input[i][1].setText(str(self.min_max_level[i][1]))
            self.minmax_level_input[i][0].blockSignals(False)
            self.minmax_level_input[i][1].blockSignals(False)

    def init_gui(self):
        layout = QVBoxLayout()
        #-----------------------------------------------------------
        hlayout = QHBoxLayout()
        label = QLabel("frequency band | rgb | min/max level (0 for auto)")
        self.loadbutton = QPushButton("load")
        self.loadbutton.clicked.connect(self.load_freqbands_jsonfile)

        hlayout.addWidget(label)
        hlayout.addWidget(self.loadbutton)
        hlayout.addStretch(1)

        label = QLabel("peak level")
        self.peak_level = QLineEdit()
        self.peak_level.setFixedWidth(80)
        self.peak_level.setReadOnly(True)
        hlayout.addWidget(label)
        hlayout.addWidget(self.peak_level)

        layout.addLayout(hlayout)

        #-----------------------------------------------------------
        # load freq bands colors and add widgets
        self.load_freq_bands_colors()
        self.freqbands_input = []
        self.freqbands_rgb_input = []
        self.minmax_level_input = []
        self.db_min = []
        self.max_level = [] # max level used for rgb intensity
        self.max_level_running = []  # max level updated every sample
        for i, (band, color) in enumerate(zip(self.freq_bands, self.freq_rgb)):
            self.dbg.tr('FREQ_BAND', f"{band} {color}")
            self.max_level.append(15)
            self.max_level_running.append(0)
            low = QLineEdit()
            high = QLineEdit()
            low.setFixedWidth(60)
            high.setFixedWidth(60)
            low.setValidator(QIntValidator(10,20000))
            high.setValidator(QIntValidator(10,20000))
            low.setText(format(band[0], '.2f'))
            high.setText(format(band[1], '.2f'))
            low.textChanged.connect(self.update_freq_bands)
            high.textChanged.connect(self.update_freq_bands)
            self.freqbands_input.append((low,high))
            self.freqbands_rgb_input.append((QLineEdit(),QLineEdit(),QLineEdit()))
            hlayout = QHBoxLayout()
            hlayout.addWidget(low)
            hlayout.addWidget(high)
            separator = QFrame()
            separator.setFrameShape(QFrame.VLine)
            hlayout.addWidget(separator)
            #-----------------------
            for j, rgb in enumerate(self.freqbands_rgb_input[-1]):
                rgb.setValidator(QDoubleValidator(0.0,5.0,2))
                rgb.setFixedWidth(40)
                rgb.setText(format(color[j], '.2f'))
                rgb.textChanged.connect(self.update_freq_rgb)
                hlayout.addWidget(rgb)
            separator = QFrame()
            separator.setFrameShape(QFrame.VLine)
            hlayout.addWidget(separator)
            #-----------------------
            min_level = QLineEdit()
            min_level.setValidator(QIntValidator(0,1000))
            min_level.setFixedWidth(50)
            min_level.setText(str(self.min_max_level[i][0]))
            min_level.textChanged.connect(self.update_min_max_level)
            max_level = QLineEdit()
            max_level.setValidator(QIntValidator(0,1000))
            max_level.setFixedWidth(50)
            max_level.setText(str(self.min_max_level[i][1]))
            max_level.textChanged.connect(self.update_min_max_level)
            hlayout.addWidget(min_level)
            hlayout.addWidget(max_level)
            self.minmax_level_input.append((min_level, max_level))
            self.db_min.append(-27)

            hlayout.addStretch(1)
            layout.addLayout(hlayout)
        layout.addStretch(1)

        #-------------------------------------------------------------------------------
        self.start_button = QPushButton("start")
        self.start_button.clicked.connect(self.start)
        layout.addWidget(self.start_button)
        self.setLayout(layout)

    def load_freq_bands_colors(self, file_name='freq_bands_colors.json'):
        try:
            with open(file_name, 'r') as file:
                freq_bands_colors = json.load(file)
                self.freq_bands = freq_bands_colors['freq_bands']
                self.freq_rgb = freq_bands_colors['colors']
                self.min_max_level = freq_bands_colors['min_max_level']
                if len(self.freq_bands) > 31:
                    raise Exception("too many freq bands")
                if len(self.freq_bands) != len(self.freq_rgb):
                    raise Exception("freq_bands and colors have different lengths")
                self.dbg.tr('FREQ_BAND', f"freq_bands:{self.freq_bands}")
                self.dbg.tr('FREQ_BAND', f"freq_rgb:{self.freq_rgb}")
                self.dbg.tr('FREQ_BAND', f"min_max_level:{self.min_max_level}")
                if self.min_max_level is None or len(self.min_max_level) != len(self.freq_bands):
                    self.min_max_level = [(0,0) for _ in range(len(self.freq_bands))]
        except Exception as e:
            self.dbg.tr('DEBUG', f"error loading file: {e}")
            self.freq_bands = []
            self.freq_rgb = []
            self.min_max_level = []

    def save_freq_bands_colors(self):
        freq_bands_colors = {
            "freq_bands": self.freq_bands,
            "colors": self.freq_rgb
        }
        with open('freq_bands_colors.json', 'w') as file:
            json.dump(freq_bands_colors, file, indent=4)

    def update_freq_rgb(self):
        n_ranges = len(self.freq_bands)
        self.freq_rgb = []
        for i in range(n_ranges):
            self.freq_rgb.append([float(self.freqbands_rgb_input[i][0].text()), float(self.freqbands_rgb_input[i][1].text()), float(self.freqbands_rgb_input[i][2].text())])

        self.dbg.tr('FREQ_BAND', f"freq band colors {self.freq_rgb}")

    def update_freq_bands(self):
        n_ranges = len(self.freq_bands)
        for i in range(n_ranges):
            self.freq_bands[i] = (float(self.freqbands_input[i][0].text()), float(self.freqbands_input[i][1].text()))

        self.dbg.tr('FREQ_BAND', f"freq bands {self.freq_bands}")
        if self.audio_thread:
            self.audio_thread.set_freq_bands(self.freq_bands)

    def update_min_max_level(self):
        n_ranges = len(self.freq_bands)
        min_max_level = []
        for i in range(n_ranges):
            min_max_level.append((int(self.minmax_level_input[i][0].text()), int(self.minmax_level_input[i][1].text())))
        self.min_max_level = min_max_level

    #-------------------------------------------------------------------------------
    def db_to_255(self, dB_value, dB_min=-60, dB_max=0):
        # Normalize dB value from [dB_min, dB_max] to [0, 1]
        normalized = (dB_value - dB_min) / (dB_max - dB_min)
        # Clip the normalized value to be within the range [0, 1]
        normalized = max(0, min(1, normalized))
        # Convert normalized value to an integer in the range [0, 255]
        mapped_value = int(round(255 * normalized))
        return mapped_value

    def peak_level_to_rgb(self, peak_levels, db_min, max_level, log_scale = True):
        r = g = b = 0
        MAX_RGB = 255
        for i in range(len(peak_levels)):
            try:
                peak_db = 20 * np.log10(peak_levels[i]/max_level[i])
                peak_db_rgb = self.db_to_255(peak_db, db_min[i], 0)
                #print(f"peak {i}: {peak_levels[i]} {peak_db} {peak_db_rgb}")
                if log_scale:
                    r += peak_db_rgb * self.freq_rgb[i][0]
                    g += peak_db_rgb * self.freq_rgb[i][1]
                    b += peak_db_rgb * self.freq_rgb[i][2]
                else:
                    r += peak_levels[i]/max_level[i] * self.freq_rgb[i][0] * MAX_RGB
                    g += peak_levels[i]/max_level[i] * self.freq_rgb[i][1] * MAX_RGB
                    b += peak_levels[i]/max_level[i] * self.freq_rgb[i][2] * MAX_RGB
            except Exception as e:
                self.dbg.tr('DEBUG', f"ppeak_level_to_rgb:{e}")
                pass # #bands updated in ui

        # rgb values are added for all bands, normalize with a factor
        r /= 6
        g /= 6
        b /= 6
        r = min(r, MAX_RGB)
        g = min(g, MAX_RGB)
        b = min(b, MAX_RGB)
        return r,g,b

    #-------------------------------------------------------------------------------
    def process_audiopeak_levels(self, peak_levels):
        if peak_levels is None:
            self.signal_rgb_image.emit(None, self.rgb_multiplier)
            return

        self.sample_count += 1
        if self.dbg.enabled('PEAK_LEVEL'):
            self.dbg.tr('PEAK_LEVEL', f"peak {self.sample_count}: {peak_levels}")

        # update "running max level", after N samples "max level" is adjusted with this
        peak_level = 0 # current sample peak level
        for i, lvl in enumerate(peak_levels):
            if lvl > self.max_level_running[i]:
                self.max_level_running[i] = lvl
            if lvl > peak_level:
                peak_level = lvl

        # update "max level" every N samples, brightness is based on current peak levels and "max level"
        if self.sample_count == 20:
            self.sample_count = 0
            max_level_running = 0
            max_level_running_band = 0
            for i in range(len(peak_levels)):
                try:
                    if self.max_level_running[i] > max_level_running:
                        max_level_running = self.max_level_running[i]
                        max_level_running_band = i

                    if self.min_max_level[i][0] == 0:
                        self.db_min[i] = -27
                    else: # user defined min level
                        self.db_min[i] = 20 * np.log10(self.min_max_level[i][0]/self.max_level[i])

                    if self.min_max_level[i][1] > 0: # user defined max level
                        self.max_level[i] = self.min_max_level[i][1]
                    else:
                        self.max_level[i] += (self.max_level_running[i] - self.max_level[i])/2
                except Exception as e:
                    self.dbg.tr('DEBUG', f"process_audiopeak_levels:{e}")
                    pass

            if self.dbg.enabled('MAX_PEAK'):
                try:
                    self.dbg.tr('MAX_PEAK', f"{time.monotonic()}:max level[{max_level_running_band}] {max_level_running}")
                except:
                    pass

            self.peak_level.setText(f"{max_level_running:.2f}")
            self.max_level_running = [0] * len(self.freq_bands)

        if all(level < 0.05 for (level) in peak_levels):
            # no audio
            return

        r,g,b = self.peak_level_to_rgb(peak_levels, self.db_min, self.max_level)
        self.keyb_rgb.fill(QColor(r,g,b))
        #-----------------------------------------------------------
        if self.running:
            #self.dbg.tr('DEBUG', f"send rgb {self.keyb_rgb}")
            self.signal_rgb_image.emit(self.keyb_rgb, self.rgb_multiplier)

    # todo: add effects
    # - mask leds per freq band/peak level
    # - trigger wave animation on freq band/peak level
    # - ...
    def apply_effect(self, img, peak_level):
        # mask image to disable leds depending on peak level
        if self.keyb_rgb_mask_mode != 0:
            img = self.keyb_rgb.convertToFormat(QImage.Format_ARGB32)
            self.keyb_rgb_mask.fill(0)
            mask_bits = self.keyb_rgb_mask.bits()
            bytes_per_line = self.keyb_rgb_mask.bytesPerLine()

            # max num leds to light up to left and right of center
            peak_max_num_leds = int(img.width()//2)
            center_led = int(img.width()//2)
            num_leds = int(min(1.0, peak_level / self.max_level) * peak_max_num_leds)
            x_range = (max(0, center_led - num_leds), min(img.width(), center_led + num_leds)+1)

            if self.keyb_rgb_mask_mode == 1:
                for x in range(x_range[0], x_range[1]):
                    # lines 1,2
                    mask_bits[1 * bytes_per_line + x] = 255
                    mask_bits[2 * bytes_per_line + x] = 255

            if self.keyb_rgb_mask_mode == 2:
                for x in range(x_range[0], x_range[1]):
                    # all lines
                    for i in range(img.height()):
                        mask_bits[i * bytes_per_line + x] = 255

            img.setAlphaChannel(self.keyb_rgb_mask)
            img = img.convertedTo(QImage.Format_RGB888)
            self.keyb_rgb = img

    def start(self):
        if not self.audio_thread:
            return

        if not self.audio_thread.isRunning():
            self.update_freq_rgb()
            self.update_freq_bands()
            self.update_min_max_level()
            self.audio_thread.connect_callback(self.process_audiopeak_levels)
            self.audio_thread.start()
            self.start_button.setText("stop")
            self.running = True
        else:
            self.audio_thread.stop()
            self.audio_thread.wait()
            self.start_button.setText("start")
            self.running = False

    def closeEvent(self, event):
        if not self.audio_thread:
            return

        if self.audio_thread.isRunning():
            self.audio_thread.stop()
            self.audio_thread.wait()

#-------------------------------------------------------------------------------

class HexEditor(QTextEdit):
    def __init__(self):
        super().__init__()
        self.setFont(QFont("Courier New", 9))
        self.setAcceptRichText(False)  # Only plain text to avoid formatting

    def keyPressEvent(self, event: QKeyEvent):
        if event.matches(QKeySequence.Paste):
            self.handlePaste()
            return

        # Allow undo
        if event.matches(QKeySequence.Undo):
            self.undo()
            return
        # Allow redo
        elif event.matches(QKeySequence.Redo):
            self.redo()
            return

        text = event.text()
        # Only allow hexadecimal characters
        if text.upper() in '0123456789ABCDEF':
            super().keyPressEvent(event)
            self.formatText()
        # Allow backspace and delete
        elif event.key() in (Qt.Key_Backspace, Qt.Key_Delete):
            super().keyPressEvent(event)
            self.formatText()
        # Ignore other keys
        else:
            event.ignore()

    def handlePaste(self):
            clipboard = QApplication.clipboard()
            text = clipboard.text()
            # Validate and clean the pasted text
            cleaned_text = ''.join(filter(lambda x: x.upper() in '0123456789ABCDEF', text.upper()))
            if cleaned_text:
                # Only insert cleaned text
                self.insertPlainText(cleaned_text)
                self.formatText()
                cursor = self.textCursor()
                cursor.movePosition(QTextCursor.End)
                self.setTextCursor(cursor)
            else:
                QMessageBox.warning(self, "Invalid Paste Content", "Pasted text contains non-hexadecimal characters.")

    def formatText(self):
        cursor_pos = self.textCursor().position()
        # Remove spaces and newlines for clean formatting
        text = self.toPlainText().replace(" ", "").replace("\n", "")
        # Insert space after every 2 hex characters and newline after every 32 characters
        formatted_text = ' '.join(text[i:i+2] for i in range(0, len(text), 2))
        formatted_text = '\n'.join(formatted_text[i:i+48] for i in range(0, len(formatted_text), 48))  # 32 hex chars + 16 spaces = 48
        self.setPlainText(formatted_text)
        cursor = self.textCursor()
        cursor.setPosition(cursor_pos)
        self.setTextCursor(cursor)

    def is_hex(self, s):
        try:
            int(s, 16)
            return True
        except ValueError:
            return False

    def getBinaryContent(self):
        # Remove spaces and newlines to get a clean hex string
        hex_str = self.toPlainText().replace(" ", "").replace("\n", "")
        try:
            # Convert hex string to a binary array (bytes object)
            data = bytes.fromhex(hex_str)
            return data
        except ValueError:
            # Handle the case where the hex string is invalid
            QMessageBox.warning(self, "Invalid Hex Content", "The content contains non-hexadecimal characters or an incomplete byte.")
            return None

class RGBDynLDAnimationTab(QWidget):
    signal_dynld_function = Signal(int, bytearray)

    def __init__(self):
        self.dbg = DebugTracer(zones={'D':1}, obj=self)
        #---------------------------------------
        super().__init__()
        self.init_gui()

    def init_gui(self):
        layout = QVBoxLayout()
        #---------------------------------------
        # dynld animation bin file
        hlayout = QHBoxLayout()
        dynld_bin_label = QLabel("animation bin")
        self.dynld_bin_input = QLineEdit("v:\shared\qmk\dynld_animation.bin")
        hlayout.addWidget(dynld_bin_label)
        hlayout.addWidget(self.dynld_bin_input)
        self.load_button = QPushButton("load")
        self.load_button.clicked.connect(self.load_dynld_animation_func)
        hlayout.addWidget(self.load_button)
        layout.addLayout(hlayout)

        #---------------------------------------
        self.dynld_funtext_edit = HexEditor()
        self.dynld_funtext_edit.setFixedHeight(400)
        self.load_dynld_animation_func()
        layout.addWidget(self.dynld_funtext_edit)

        #---------------------------------------
        self.send_button = QPushButton("send to keyboard")
        self.send_button.clicked.connect(self.send_dynld_animation_func)
        layout.addWidget(self.send_button)
        layout.addStretch(1)
        self.setLayout(layout)

    def load_dynld_animation_func(self):
        try:
            with open(self.dynld_bin_input.text(), 'rb') as file:
                buf = bytearray(file.read())
                hexbuf = buf.hex(' ')
                self.dynld_funtext_edit.setText(hexbuf)
                self.dynld_funtext_edit.formatText()
        except Exception as e:
            self.dbg.tr('DEBUG', f"error: {e}")

    def send_dynld_animation_func(self):
        fundata = self.dynld_funtext_edit.getBinaryContent()
        if fundata:
            DYNLD_ANIMATION_FUNC = 0
            self.signal_dynld_function.emit(DYNLD_ANIMATION_FUNC, fundata)

#-------------------------------------------------------------------------------
class ProgramSelectorComboBox(QComboBox):
    class TabDelegate(QStyledItemDelegate):
        def paint(self, painter, option, index):
            text = index.data().replace("\t", " ")
            painter.drawText(option.rect, text)

    def __init__(self, winfocusText=None):
        super().__init__(None)
        self.winfocusText = winfocusText
        self.setItemDelegate(self.TabDelegate())

    def mousePressEvent(self, event: QMouseEvent):
        super().mousePressEvent(event)

        if event.button() == Qt.LeftButton:
            if self.winfocusText:
                self.clear()
                lines = self.winfocusText.toPlainText().split('\n')
                for line in lines:
                    self.addItem(line.strip())
                self.addItem("-")
                #print(self.winfocusText.toPlainText())

class LayerAutoSwitchTab(QWidget):
    signal_keyb_set_layer = Signal(int)
    num_program_selectors = 4

    def __init__(self, num_keyb_layers=8):
        self.dbg = DebugTracer(zones={'D':0}, obj=self)

        self.current_layer = 0
        self.num_keyb_layers = num_keyb_layers
        self.ws_server = None

        super().__init__()
        self.init_gui()

    async def ws_handler(self, websocket, path):
        async for message in websocket:
            self.dbg.tr('DEBUG', f"ws_handler: {message}")
            if message.startswith("layer:"):
                try:
                    layer = int(message.split(":")[1])
                    self.signal_keyb_set_layer.emit(layer)
                except Exception as e:
                    self.dbg.tr('DEBUG', f"ws_handler: {e}")

    def ws_server_startstop(self, state):
        #self.dbg.tr('DEBUG', f"{state}")
        if Qt.CheckState(state) == Qt.CheckState.Checked:
            self.ws_server = WSServer(self.ws_handler, int(self.layer_switch_server_port.text()))
            self.ws_server.start()
        else:
            try:
                self.ws_server.stop()
                self.ws_server.wait()
                self.ws_server = None
            except Exception as e:
                self.dbg.tr('DEBUG', f"{e}")

    async def ws_handler(self, websocket, path):
        async for message in websocket:
            self.dbg.tr('DEBUG', f"ws_handler: {message}")
            if message.startswith("layer:"):
                try:
                    layer = int(message.split(":")[1])
                    self.signal_keyb_set_layer.emit(layer)
                except Exception as e:
                    self.dbg.tr('DEBUG', f"ws_handler: {e}")

    def init_gui(self):
        layout = QVBoxLayout()
        layout.setAlignment(Qt.AlignTop)

        #---------------------------------------
        # default layer
        self.deflayer_label = QLabel("default layer")
        metrics = QFontMetrics(self.deflayer_label.font())
        self.deflayer_label.setFixedHeight(metrics.height())

        layout.addWidget(self.deflayer_label)
        # QComboBox for selecting layer
        self.deflayer_selector = QComboBox()
        self.deflayer_selector.addItems([str(i) for i in range(self.num_keyb_layers)])
        layout.addWidget(self.deflayer_selector)
        self.deflayer_selector.setCurrentIndex(0)
        #---------------------------------------
        # instruction summary
        self.label = QLabel("select default layer above, the foreground application is traced here below.\n"
                            "select program(s) and the layer to use in the dropdown box below.\n"
                            "select '-' to unselect program.\n"
                            "\n"
                            "enabling \"layer switch ws server\" allow applications to send layer switch requests\n"
                            "by sending \"layer:<number>\" to \"ws://localhost:<port>\"\n"
                            )
        layout.addWidget(self.label)
        #---------------------------------------
        # "layer switch ws server" enable checkbox plus port input
        self.layer_switch_server_checkbox = QCheckBox("enable ws server", self)
        self.layer_switch_server_port = QLineEdit("8765")
        port_validator = QIntValidator(0, 65535, self)
        self.layer_switch_server_port.setValidator(port_validator)
        self.layer_switch_server_port.setFixedWidth(50)
        self.layer_switch_server_checkbox.stateChanged.connect(self.ws_server_startstop)
        hlayout = QHBoxLayout()
        hlayout.addStretch(1)
        hlayout.addWidget(self.layer_switch_server_checkbox)
        hlayout.addWidget(self.layer_switch_server_port)
        layout.addLayout(hlayout)

        #---------------------------------------
        # for displaying processes which got foreground focus
        self.winfocus_textedit = QTextEdit()
        self.winfocus_textedit.setReadOnly(True)
        self.winfocus_textedit.setMaximumHeight(180)  # Adjust the height
        self.winfocus_textedit.textChanged.connect(self.limit_lines)
        layout.addWidget(self.winfocus_textedit)

        #---------------------------------------
        self.program_selector = []
        self.layer_selector = []
        for i in range(self.num_program_selectors):
            self.program_selector.append(ProgramSelectorComboBox(self.winfocus_textedit))
            self.program_selector[i].addItems(["" for i in range(5)])
            self.program_selector[i].setCurrentIndex(0)
            layout.addWidget(self.program_selector[i])

            self.layer_selector.append(QComboBox())
            self.layer_selector[i].addItems([str(i) for i in range(self.num_keyb_layers)])
            self.layer_selector[i].setCurrentIndex(0)
            layout.addWidget(self.layer_selector[i])
        #---------------------------------------
        self.setLayout(layout)

    def update_default_layer(self, layer):
        self.dbg.tr('DEBUG', f"default layer update: {layer}")
        self.deflayer_selector.setCurrentIndex(layer)

    def on_winfocus(self, line):
        self.update_winfocus_text(line)
        self.current_focus = line
        # foreground focus window info
        focus_win = line.split("\t")
        #self.dbg.tr('DEBUG', f"on_winfocus {focus_win}")
        for i, ps in enumerate(self.program_selector):
            compare_win = self.program_selector[i].currentText().split("\t")
            #self.dbg.tr('DEBUG', f"on_winfocus compare: {compare_win}")
            if focus_win[0].strip() == compare_win[0].strip() and \
               focus_win[1].strip() == compare_win[1].strip():
                layer = int(self.layer_selector[i].currentText())
                self.signal_keyb_set_layer.emit(layer)
                self.current_layer = layer
                self.dbg.tr('DEBUG', f"layer set: {layer}")
                return

        defaultLayer = self.deflayer_selector.currentIndex()
        if self.current_layer != defaultLayer:
            self.signal_keyb_set_layer.emit(defaultLayer)
            self.current_layer = defaultLayer
            self.dbg.tr('DEBUG', f"layer set: {defaultLayer}")

    def update_winfocus_text(self, line):
        self.winfocus_textedit.append(line)

    def limit_lines(self):
        lines = self.winfocus_textedit.toPlainText().split('\n')
        if len(lines) > 10:
            self.winfocus_textedit.setPlainText('\n'.join(lines[-10:]))

    def closeEvent(self, event):
        if self.ws_server:
            self.ws_server.stop()
            self.ws_server.wait()

#-------------------------------------------------------------------------------
class TreeviewWidget(QWidget):

    def __init__(self, keyboard_model):
        self.keyboard_model = keyboard_model
        self.endian = 'little'
        try:
            if self.keyboard_model.MCU[2].startswith("be"):
                self.endian = 'big'
        except:
            pass
        super().__init__()

    def init_gui(self):
        if not hasattr(self, 'layout'):
            self.layout = QVBoxLayout()

        # default tree view
        model = QStandardItemModel()
        self.tree_view = QTreeView()
        self.tree_view.setModel(model)
        self.tree_view.setFixedHeight(800)
        self.layout.addWidget(self.tree_view)
        self.layout.addStretch(1)
        self.setLayout(self.layout)

    def update_view_model(self, view_model):
        self.dbg.tr('DEBUG', f"update_view_model: {view_model}")
        self.tree_view.setModel(view_model)
        if view_model:
            try:
                view_model.dataChanged.connect(self.update_keyb_data)
            except Exception as e:
                self.dbg.tr('DEBUG', f"update_view_model: {e}")

    def update_view(self, item_data): # update from firmata keyboard
        try:
            item_id = item_data[0]
        except Exception as e:
            self.dbg.tr('DEBUG', f"update_view: {e}, {item_data}")
            return
        field_values = item_data[1]
        model = self.tree_view.model()
        item = model.item(item_id-1, 0)
        self.dbg.tr('DEBUG', f"update_view: {item.text()} {item_data}")
        for i in range(item.rowCount()): # todo: row number may not match field id
            try:
                value_item = item.child(i, 3)
                type_item = item.child(i, 1)
                field_value = field_values[i+1]
                if type(field_value) == bytearray:
                    value_item.setFont(QFont("Courier New", 7)) # todo move this to update_view_model
                    #self.dbg.tr('DEBUG', f"update_view: {type_item.text()}")
                    hex_string = ''
                    if type_item.text().endswith("uint8"):
                        item_size = 1
                        items_per_line = 16
                        format_str = "%02x "
                    elif type_item.text().endswith("uint16"):
                        item_size = 2
                        items_per_line = 10
                        format_str = "%04x "
                    elif type_item.text().endswith("uint32"):
                        item_size = 4
                        items_per_line = 8
                        format_str = "%08x "
                    elif type_item.text().endswith("uint64"):
                        item_size = 8
                        items_per_line = 4
                        format_str = "%016x "

                    if item.text() == "keymap layout":
                        items_per_line = self.keyboard_model.matrix_size()[0]

                    for i in range(0, len(field_value), item_size):
                        val = int.from_bytes(field_value[i:i+item_size], self.endian)
                        hex_string += format_str % val
                        #hex_string += f"{val:02x} "
                        if i % (items_per_line*item_size) == (items_per_line*item_size) - item_size:
                            hex_string += '\n'

                    formatted_string = hex_string
                    field_value = formatted_string
                    #field_value = field_value.hex(' ')
                value_item.setText(f"{field_value}")
            except Exception as e:
                self.dbg.tr('DEBUG', f"update_view: {e}")

#-------------------------------------------------------------------------------
class KeybConfigTab(TreeviewWidget):
    signal_keyb_set_config = Signal(tuple)
    signal_keyb_get_config = Signal(int)
    signal_macwin_mode = Signal(str)

    def __init__(self, keyboard_model):
        self.dbg = DebugTracer(zones={'D':0}, obj=self)

        self.keyboard_model = keyboard_model
        super().__init__(keyboard_model)
        self.init_gui()

    def update_keyb_data(self, tl, br, roles):
        #self.dbg.tr('DEBUG', f"update_keyb_data: topleft:{tl}, roles:{roles}")
        update = False
        if roles:
            for role in roles:
                if role == Qt.EditRole:
                    update = True

        if update: # todo update only if changed via ui not from update_view
            try:
                item = self.tree_view.model().itemFromIndex(tl)
                config_item = item.parent()
                config_id = config_item.row() + 1

                field_values = {}
                for i in range(config_item.rowCount()):
                    field = config_item.child(i, 0)
                    value = config_item.child(i, 3)
                    #print(f"{field.text()} = {value.text()}")
                    if value.text() == "":
                        return
                    if not value.flags() & Qt.ItemIsEditable:
                        return
                    field_values[i+1] = value.text()
            except Exception as e:
                self.dbg.tr('DEBUG', f"update_keyb_config: {e}")
                return
            config = (config_id, field_values)
            self.dbg.tr('DEBUG', f"update_keyb_config:signal emit {config}")
            self.signal_keyb_set_config.emit(config)

    def update_keyb_macwin_mode(self):
        macwin_mode = self.mac_win_mode_selector.currentText()
        self.signal_macwin_mode.emit(macwin_mode)

    def init_gui(self):
        hlayout = QHBoxLayout()
        config_label = QLabel("keyboard configuration")
        config_refresh_button = QPushButton("refresh")
        config_refresh_button.clicked.connect(lambda: self.signal_keyb_get_config.emit(0))
        hlayout.addWidget(config_label)
        hlayout.addWidget(config_refresh_button)
        hlayout.addStretch(1)
        #---------------------------------------
        # mac/win mode
        macwin_label = QLabel("mac/win mode")
        self.mac_win_mode_selector = QComboBox()
        self.mac_win_mode_selector.addItem('m')
        self.mac_win_mode_selector.addItem('w')
        self.mac_win_mode_selector.addItem('-')
        hlayout.addWidget(macwin_label)
        hlayout.addWidget(self.mac_win_mode_selector)
        self.mac_win_mode_selector.setCurrentIndex(1)
        self.mac_win_mode_selector.currentIndexChanged.connect(self.update_keyb_macwin_mode)
        self.mac_win_mode_selector.setFixedWidth(40)
        hlayout.addStretch(1)

        self.layout = QVBoxLayout()
        self.layout.addLayout(hlayout)
        super().init_gui()

    def update_macwin_mode(self, macwin_mode):
        self.mac_win_mode_selector.setCurrentIndex(0 if macwin_mode == 'm' else 1)

#-------------------------------------------------------------------------------
class KeybStatusTab(TreeviewWidget):
    signal_keyb_get_status = Signal(int, int)

    def __init__(self, keyboard_model):
        self.dbg = DebugTracer(zones={'D':0}, obj=self)

        self.keyboard_model = keyboard_model
        super().__init__(keyboard_model)
        self.init_gui()

    def init_gui(self):
        hlayout = QHBoxLayout()
        status_label = QLabel("keyboard status")
        status_refresh_button = QPushButton("refresh")
        every_label = QLabel("every (ms)")
        every_msec_edit = QLineEdit("100")
        every_msec_edit.setValidator(QIntValidator(0, 60000))
        every_msec_edit.setFixedWidth(50)
        status_refresh_button.clicked.connect(lambda: self.signal_keyb_get_status.emit(int(every_msec_edit.text()), 0))

        hlayout.addWidget(status_label)
        hlayout.addWidget(status_refresh_button)
        hlayout.addWidget(every_label)
        hlayout.addWidget(every_msec_edit)
        hlayout.addStretch(1)

        self.layout = QVBoxLayout()
        self.layout.addLayout(hlayout)
        super().init_gui()

#-------------------------------------------------------------------------------
class KeybScriptTab(QWidget):
    signal_run_script = Signal(str, object)
    signal_script_output = Signal(str)

    def __init__(self, keyboard_model):
        self.dbg = DebugTracer(zones={'D':0}, obj=self)
        #---------------------------------------
        self.keyboard_model = keyboard_model

        super().__init__()
        self.init_gui()
        self.signal_script_output.connect(self.append_script_output)

    def append_script_output(self, text):
        try:
            cursor = self.script_output.textCursor()
            cursor.movePosition(QTextCursor.End)
            self.script_output.setTextCursor(cursor)
            self.script_output.insertPlainText(text)
            self.script_output.ensureCursorVisible()
        except:
            pass

    def exec_script(self):
        self.script_output.clear()
        script = self.code_editor.toPlainText()
        self.signal_run_script.emit(script, self.signal_script_output)

    def init_gui(self):
        layout = QVBoxLayout()

        self.exec_button = QPushButton("exec")
        self.exec_button.clicked.connect(self.exec_script)
        self.code_editor = CodeTextEdit()
        self.script_output = QTextEdit()
        self.script_output.setReadOnly(True)
        font = QFont()
        font.setFamily("Courier New")
        self.script_output.setFont(font)

        layout.addWidget(self.exec_button)
        layout.addWidget(self.code_editor)
        layout.addWidget(self.script_output)
        self.setLayout(layout)

#-------------------------------------------------------------------------------
from matplotlib.figure import Figure
from matplotlib.patches import Rectangle
import matplotlib.pyplot as plt
import matplotlib.animation as animation

from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas

def add_method_to_class(class_def, method):
    method_definition = method
    # Execute the method definition and retrieve the method from the local scope
    local_scope = {}
    exec(method_definition, globals(), local_scope)
    for method in list(local_scope.values()):
        #print(f"{method.__name__} added to class {class_def.__name__}")
        # Add the method to the class
        setattr(class_def, method.__name__, method)

class CodeTextEdit(QTextEdit):
    def __init__(self, filepath=None, parent=None):
        super().__init__(parent)
        self.setFont(QFont("Courier New", 9))
        if filepath:
            self.load_text_file(filepath)

    def insertFromMimeData(self, source):
        if source.hasText():
            # only plain text
            plain_text = source.text()
            self.insertPlainText(plain_text)
        else:
            super().insertFromMimeData(source)

    def load_text_file(self, filepath):
        try:
            with open(filepath, "r", encoding="utf-8") as file:
                content = file.read()
                self.setPlainText(content)
        except Exception as e:
            print(f"Error opening {filepath}: {e}")

    def keyPressEvent(self, event: QKeyEvent):
        if event.key() == Qt.Key_Tab:
            # four spaces instead of a tab
            self.insertPlainText("    ")
        else:
            super().keyPressEvent(event)

class RGBAnimationTab(QWidget):
    signal_rgb_image = Signal(QImage, object)

    def __init__(self, rgb_matrix_size):
        self.dbg = DebugTracer(zones={'DEBUG': 0}, obj=self)
        self.rgb_matrix_size = rgb_matrix_size
        super().__init__()
        self.init_gui()

    def init_gui(self):
        # Create a figure for plotting
        self.figure = Figure(facecolor='black')
        self.figure.subplots_adjust(left=0, right=1, bottom=0, top=1)  # Adjust margins
        self.canvas = FigureCanvas(self.figure)
        self.ax = self.figure.add_subplot(111)
        self.ax.set_facecolor('black')
        self.ax.axis('off')

        # start animation button
        self.start_button = QPushButton("start")
        self.start_button.clicked.connect(self.start_animation)

        # Layout to hold the canvas and buttons
        layout = QVBoxLayout()
        self.code_editor = CodeTextEdit("animation.py")
        layout.addWidget(self.code_editor)
        layout.addWidget(self.canvas)
        layout.addWidget(self.start_button)
        self.setLayout(layout)

        dpi = 100
        width = 800
        height = int((self.rgb_matrix_size[1]/self.rgb_matrix_size[0]) * width)
        w_inch = width/dpi
        h_inch = height/dpi
        self.figure.set_size_inches((w_inch, h_inch))
        self.figure.set_dpi(dpi)

        width, height = self.figure.get_size_inches() * self.figure.get_dpi()
        self.dbg.tr('DEBUG', f"figure size:{width}x{height} dpi:{self.figure.get_dpi()}")

        # Parameters for the animation
        self.x_size = 20
        self.frames = 500
        self.interval = 40
        # Animation placeholder
        self.ani = None

    def start_animation(self):
        if self.ani is None:  # Prevent multiple instances if already running
            add_method_to_class(RGBAnimationTab, self.code_editor.toPlainText())
            try:
                init_fn_name, animate_fn_name = self.animate_methods()
                animate_init_method = getattr(RGBAnimationTab, init_fn_name)
                animate_method = getattr(RGBAnimationTab, animate_fn_name)
                setattr(RGBAnimationTab, "animate_init", animate_init_method)
                setattr(RGBAnimationTab, "animate", animate_method)
                self.animate_init()
                self.ani = animation.FuncAnimation(self.figure, self._animate, frames=self.frames, #init_func=self.init,
                                                blit=True, interval=self.interval, repeat=True)
            except Exception as e:
                print(e)

            if self.ani:
                self.start_button.setText("stop")
        else:
            self.ani.event_source.stop()
            self.ani = None
            self.signal_rgb_image.emit(None, (0,0,0))
            self.start_button.setText("start")

    def capture_animation_frame(self):
        if self.ani == None:
            return
        self.ani.pause()

        # capture the current frame from the canvas
        rgba_buffer = np.frombuffer(self.figure.canvas.buffer_rgba(), dtype=np.uint8)
        width, height = self.figure.get_size_inches() * self.figure.get_dpi()
        #if rgba_buffer.nbytes != width * height * 4:
            #self.dbg.tr('DEBUG', f"buffer size mismatch: {rgba_buffer.nbytes} != {width}x{height}x4")
        rgba_array = rgba_buffer.reshape(int(height), int(width), 4, order='C')
        rgba_qimg = QImage(rgba_array.data, width, height, QImage.Format_RGBA8888)
        keyb_rgb = rgba_qimg.scaled(self.rgb_matrix_size[0], self.rgb_matrix_size[1], Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.FastTransformation)
        keyb_rgb = keyb_rgb.convertToFormat(QImage.Format_RGB888)
        self.signal_rgb_image.emit(keyb_rgb, (1.0,1.0,1.0))

        self.ani.resume()

    def _animate(self, i):
        ret = self.animate(i)
        if i == self.frames:
            self.figure.clear()

        QTimer.singleShot(0, self.capture_animation_frame)
        return ret

    def closeEvent(self, event):
        if self.ani:
            self.ani.event_source.stop()
            self.ani = None

#-------------------------------------------------------------------------------
app_width       = 800
app_height      = 900

class MainWindow(QMainWindow):
    def __init__(self, keyboard_vid_pid):
        self.keyboard_vid_pid = keyboard_vid_pid
        super().__init__()
        self.init_gui()

    def init_gui(self):
        self.setWindowTitle('QMK Firmata')
        self.setGeometry(100, 100, app_width, app_height)
        self.setFixedSize(app_width, app_height)

        # instantiate firmata keyboard
        self.keyboard = FirmataKeyboard(port=None, vid_pid=self.keyboard_vid_pid)
        num_keyb_layers = self.keyboard.num_layers()

        #-----------------------------------------------------------
        # add tabs
        tab_widget = QTabWidget()
        self.console_tab = ConsoleTab(self.keyboard.keyboardModel)
        self.rgb_matrix_tab = RGBMatrixTab(self.keyboard.keyboardModel)
        self.layer_switch_tab = LayerAutoSwitchTab(num_keyb_layers)
        self.keyb_config_tab = KeybConfigTab(self.keyboard.keyboardModel)
        self.keyb_status_tab = KeybStatusTab(self.keyboard.keyboardModel)
        self.keyb_script_tab = KeybScriptTab(self.keyboard.keyboardModel)

        tab_widget.addTab(self.console_tab, 'console')
        tab_widget.addTab(self.keyb_script_tab, 'keyboard script')
        tab_widget.addTab(self.rgb_matrix_tab, 'rgb matrix')
        tab_widget.addTab(self.layer_switch_tab, 'layer auto switch')
        tab_widget.addTab(self.keyb_config_tab, 'keyboard config')
        tab_widget.addTab(self.keyb_status_tab, 'keyboard status')

        self.setCentralWidget(tab_widget)
        #-----------------------------------------------------------
        # connect signals
        self.keyboard.signal_console_output.connect(self.console_tab.update_text)
        self.keyboard.signal_default_layer.connect(self.layer_switch_tab.update_default_layer)
        self.keyboard.signal_macwin_mode.connect(self.keyb_config_tab.update_macwin_mode)
        self.keyboard.signal_config_model.connect(self.keyb_config_tab.update_view_model)
        self.keyboard.signal_status_model.connect(self.keyb_status_tab.update_view_model)
        self.keyboard.signal_config.connect(self.keyb_config_tab.update_view)
        self.keyboard.signal_status.connect(self.keyb_status_tab.update_view)

        self.console_tab.signal_cli_command.connect(self.keyboard.keyb_set_cli_command)
        self.rgb_matrix_tab.rgb_video_tab.signal_rgb_image.connect(self.keyboard.keyb_set_rgb_image)
        self.rgb_matrix_tab.rgb_animation_tab.signal_rgb_image.connect(self.keyboard.keyb_set_rgb_image)
        self.rgb_matrix_tab.rgb_audio_tab.signal_rgb_image.connect(self.keyboard.keyb_set_rgb_image)
        self.rgb_matrix_tab.rgb_dynld_animation_tab.signal_dynld_function.connect(self.keyboard.keyb_set_dynld_function)
        self.layer_switch_tab.signal_keyb_set_layer.connect(self.keyboard.keyb_set_default_layer)
        self.keyb_config_tab.signal_keyb_set_config.connect(self.keyboard.keyb_set_config)
        self.keyb_config_tab.signal_keyb_get_config.connect(self.keyboard.keyb_get_config)
        self.keyb_config_tab.signal_macwin_mode.connect(self.keyboard.keyb_set_macwin_mode)
        self.keyb_script_tab.signal_run_script.connect(self.keyboard.run_script)
        self.keyb_status_tab.signal_keyb_get_status.connect(self.keyboard.keyb_get_status)

        #-----------------------------------------------------------
        # window focus listener
        try:
            self.winfocus_listener = WinFocusListener()
            self.winfocus_listener.signal_winfocus.connect(self.layer_switch_tab.on_winfocus)
            self.winfocus_listener.start()
        except:
            pass

        #-----------------------------------------------------------
        # start keyboard communication
        self.keyboard.start()

    def closeEvent(self, event):
        try:
            self.winfocus_listener.stop()
        except:
            pass
        self.keyboard.stop()
        # close event to child widgets
        for child in self.findChildren(QWidget):
            child.closeEvent(event)
        event.accept()

class KeyboardSelectionPopup(QMessageBox):
    def __init__(self, keyboards):
        super().__init__()
        self.setWindowTitle('select keyboard')
        self.setText('keyboard:')

        # dropdown (combo box) for keyboard selection
        self.comboBox = QComboBox()
        self.comboBox.addItems(keyboards)

        # Add the combo box to the QMessageBox layout
        layout = self.layout()
        layout.addWidget(self.comboBox, 1, 1, 1, layout.columnCount())

        # Add an OK button
        self.addButton(QMessageBox.Ok)

        # Connect the OK button click to a handler (this example uses lambda for simplicity)
        self.buttonClicked.connect(lambda: self.accept())

    def selected_keyboard(self):
        return self.comboBox.currentText()

def detect_keyboards():
    hid_devices = hid.enumerate()
    def device_attached(vendor_id, product_id):
        for device in hid_devices:
            if device['vendor_id'] == vendor_id and device['product_id'] == product_id:
                return True
        return False

    keyboard_models = FirmataKeyboard.load_keyboard_models()
    keyboards = []
    print (f"keyboards: {keyboard_models[0]}")
    for model in keyboard_models[0].values():
        if device_attached(model.VID, model.PID):
            print (f"keyboard found: {model.NAME} ({hex(model.VID)}:{hex(model.PID)})")
            keyboards.append(model.NAME)

    return keyboards, keyboard_models[0]

#-------------------------------------------------------------------------------
def main(keyboard_vid_pid):
    from PySide6.QtCore import QLocale
    locale = QLocale("C")
    QLocale.setDefault(locale)
    app = QApplication(sys.argv)
    #app.setStyle('Windows')
    app.setStyle('Fusion')

    selected_keyboard = ""
    if keyboard_vid_pid[0] == None:
        keyboards, keyb_models = detect_keyboards()
        if len(keyboards):
            selection_popup = KeyboardSelectionPopup(keyboards)
            if selection_popup.exec():
                selected_keyboard = selection_popup.selected_keyboard()
                keyboard_vid_pid = keyb_models[selected_keyboard].VID, keyb_models[selected_keyboard].PID

    main_window = MainWindow(keyboard_vid_pid)
    main_window.show()
    sys.exit(app.exec())

parser = argparse.ArgumentParser(description="keyboard vendor/product id")
parser.add_argument('--vid', required=False, type=lambda x: int(x, 16),
                    help='keyboard vid in hex')
parser.add_argument('--pid', required=False, type=lambda x: int(x, 16),
                    help='keyboard pid in hex')
args = parser.parse_args()

main((args.vid, args.pid))