import sys
import os
import zipfile
import tempfile
import shutil
import fitz
import requests
import hashlib
import subprocess
import time
import uuid
from urllib.parse import urlparse

from PyQt6.QtWidgets import (
    QApplication,
    QMainWindow,
    QLabel,
    QWidget,
    QVBoxLayout,
    QPushButton,
    QFileDialog,
    QComboBox,
    QMessageBox,
    QProgressDialog,
)
from PyQt6.QtGui import QFont, QIcon
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QTimer

from file_formating_utils.vvdn_utils import vvdn_pdf
from file_formating_utils.asm_utils import asm_pdf
from file_formating_utils.sanmina_utils import sanmina_pdf
from file_formating_utils.solinas_utils import solinas_pdf
from file_formating_utils.anora_untils import anora_pdf

from packaging.version import Version

APP_NAME = "File Formatter"
APP_VERSION = "1.0.0"
VERSION_URL = "https://saralaquote.saralaengineering.in/uploader/ota/app/version/"
DOWNLOAD_CHUNK_SIZE = 1024 * 1024


# ============================================================
# PROCESS ZIP
# ============================================================

def run_updater():
    if len(sys.argv) < 5:
        return False

    if sys.argv[1] != "--update":
        return False

    try:
        old_pid = int(sys.argv[2])
        new_file = os.path.abspath(sys.argv[3])
        target_file = os.path.abspath(sys.argv[4])
    except (TypeError, ValueError):
        return False

    # Wait for the old application to close
    # The updater is a temporary copy of this executable.  The original
    # process must be gone before Windows will allow its EXE to be replaced.
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        try:
            os.kill(old_pid, 0)
            time.sleep(0.5)
        except OSError:
            break
    else:
        return False

    # Replace old application
    try:
        # os.replace is atomic where possible and does not leave the
        # application without an executable if the replacement fails.
        os.replace(new_file, target_file)

        subprocess.Popen(
            [target_file],
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP
            | subprocess.DETACHED_PROCESS
        )

    except (OSError, subprocess.SubprocessError):
        return False

    return True

if run_updater():
    sys.exit(0)


def validate_release(data):
    """Return a safe Windows release manifest, or None when it is invalid."""
    if not isinstance(data, dict):
        return None

    version = data.get("version")
    download_url = data.get("windows_download_url")
    checksum = data.get("windows_sha256")

    if not all(isinstance(value, str) and value for value in (version, download_url, checksum)):
        return None

    try:
        Version(version)
    except ValueError:
        return None

    parsed_url = urlparse(download_url)
    if parsed_url.scheme != "https" or not parsed_url.netloc:
        return None

    if len(checksum) != 64 or any(char not in "0123456789abcdefABCDEF" for char in checksum):
        return None

    return data


def check_for_update():
    try:
        response = requests.get(
            VERSION_URL,
            timeout=5
        )

        response.raise_for_status()

        data = validate_release(response.json())
        if not data:
            return None

        current = Version(APP_VERSION)
        latest = Version(data["version"])

        if latest > current:
            return data

        return None

    except (requests.RequestException, ValueError):
        return None


class UpdateDownloadWorker(QThread):
    """Downloads and verifies an update without freezing the PyQt window."""

    progress_changed = pyqtSignal(int)
    finished_successfully = pyqtSignal(str)
    failed = pyqtSignal(str)

    def __init__(self, download_url, expected_hash, parent=None):
        super().__init__(parent)
        self.download_url = download_url
        self.expected_hash = expected_hash.lower()

    def run(self):
        partial_path = None
        try:
            response = requests.get(
                self.download_url,
                stream=True,
                timeout=(10, 60),
            )
            response.raise_for_status()
            total_bytes = int(response.headers.get("content-length", 0))
            downloaded_bytes = 0
            hasher = hashlib.sha256()
            partial_path = os.path.join(
                tempfile.gettempdir(),
                f"file-formatter-update-{uuid.uuid4().hex}.exe.part",
            )

            with open(partial_path, "wb") as update_file:
                for chunk in response.iter_content(chunk_size=DOWNLOAD_CHUNK_SIZE):
                    if self.isInterruptionRequested():
                        raise InterruptedError("The update download was cancelled.")
                    if not chunk:
                        continue
                    update_file.write(chunk)
                    hasher.update(chunk)
                    downloaded_bytes += len(chunk)
                    if total_bytes:
                        self.progress_changed.emit(min(100, int(downloaded_bytes * 100 / total_bytes)))

            if hasher.hexdigest().lower() != self.expected_hash:
                raise ValueError("The downloaded file did not match the published SHA-256 checksum.")

            update_path = partial_path.removesuffix(".part")
            os.replace(partial_path, update_path)
            partial_path = None
            self.progress_changed.emit(100)
            self.finished_successfully.emit(update_path)
        except InterruptedError as error:
            self.failed.emit(str(error))
        except (OSError, ValueError, requests.RequestException) as error:
            self.failed.emit(f"Unable to download the update: {error}")
        finally:
            if partial_path:
                try:
                    os.remove(partial_path)
                except OSError:
                    pass



def start_updater(update_file):
    if not getattr(sys, "frozen", False):
        return False

    current_exe = os.path.abspath(sys.executable)

    updater_file = os.path.join(
        tempfile.gettempdir(),
        f"file-formatter-updater-{uuid.uuid4().hex}.exe",
    )

    target_file = current_exe

    try:
        shutil.copy2(current_exe, updater_file)

        subprocess.Popen(
            [
                updater_file,
                "--update",
                str(os.getpid()),
                update_file,
                target_file
            ],
            creationflags=(
                subprocess.CREATE_NEW_PROCESS_GROUP
                | subprocess.DETACHED_PROCESS
            )
        )

        return True

    except (OSError, subprocess.SubprocessError):
        return False

def process_zip(
    uploaded_zip_path,
    assembly_client,
    output_zip_path
):
    """
    Process the uploaded ZIP file and create the
    processed ZIP at output_zip_path.
    """

    if not uploaded_zip_path:
        raise ValueError("No ZIP file selected.")

    if not os.path.exists(uploaded_zip_path):
        raise FileNotFoundError("Selected ZIP file does not exist.")

    # ========================================================
    # Temporary directories
    # ========================================================

    with tempfile.TemporaryDirectory() as extract_dir, \
         tempfile.TemporaryDirectory() as processed_dir:

        # ====================================================
        # Extract ZIP
        # ====================================================

        with zipfile.ZipFile(
            uploaded_zip_path,
            "r"
        ) as zip_ref:

            zip_ref.extractall(extract_dir)

        # ====================================================
        # Create merged PDF
        # ====================================================

        merged_pdf = fitz.open()

        # ====================================================
        # Walk through extracted files
        # ====================================================

        for root, _, files in os.walk(extract_dir):

            files.sort()

            for file_name in files:

                source_path = os.path.join(
                    root,
                    file_name
                )

                relative_path = os.path.relpath(
                    source_path,
                    extract_dir
                )

                destination_path = os.path.join(
                    processed_dir,
                    relative_path
                )

                # Make destination directory
                os.makedirs(
                    os.path.dirname(destination_path),
                    exist_ok=True
                )

                # =================================================
                # Non-PDF files
                # =================================================

                if not file_name.lower().endswith(".pdf"):

                    shutil.copy2(
                        source_path,
                        destination_path
                    )

                    continue

                # =================================================
                # PDF file
                # =================================================

                temp_pdf_path = os.path.join(
                    processed_dir,
                    f"processed_{file_name}"
                )

                # =================================================
                # Select client processing function
                # =================================================

                if assembly_client == "vvdn":

                    vvdn_pdf(
                        source_path,
                        temp_pdf_path
                    )

                elif assembly_client == "asm":

                    asm_pdf(
                        source_path,
                        temp_pdf_path
                    )

                elif assembly_client == "sanmina":

                    sanmina_pdf(
                        source_path,
                        temp_pdf_path
                    )

                elif assembly_client == "anora":

                    anora_pdf(
                        source_path,
                        temp_pdf_path
                    )

                elif assembly_client == "solinas":

                    solinas_pdf(
                        source_path,
                        temp_pdf_path
                    )

                else:

                    # Unknown client.
                    # Copy original PDF.
                    shutil.copy2(
                        source_path,
                        temp_pdf_path
                    )

                # =================================================
                # Put processed PDF in original location
                # =================================================

                shutil.copy2(
                    temp_pdf_path,
                    destination_path
                )

                # =================================================
                # Add processed PDF to merged PDF
                # =================================================

                processed_pdf = fitz.open(
                    temp_pdf_path
                )

                merged_pdf.insert_pdf(
                    processed_pdf
                )

                processed_pdf.close()

                # Remove temporary processed PDF
                os.remove(
                    temp_pdf_path
                )

        # ====================================================
        # Create merged PDF
        # ====================================================

        merged_pdf_path = os.path.join(
            processed_dir,
            "merged.pdf"
        )

        merged_pdf.save(
            merged_pdf_path
        )

        merged_pdf.close()

        # ====================================================
        # Create output ZIP
        # ====================================================

        # Make sure output directory exists
        output_directory = os.path.dirname(
            output_zip_path
        )

        if output_directory:
            os.makedirs(
                output_directory,
                exist_ok=True
            )

        with zipfile.ZipFile(
            output_zip_path,
            "w",
            zipfile.ZIP_DEFLATED
        ) as zip_ref:

            for root, _, files in os.walk(
                processed_dir
            ):

                for file_name in files:

                    file_path = os.path.join(
                        root,
                        file_name
                    )

                    archive_name = os.path.relpath(
                        file_path,
                        processed_dir
                    )

                    zip_ref.write(
                        file_path,
                        archive_name
                    )


def resource_path(filename):
    base_path = getattr(sys, "_MEIPASS", os.path.abspath("."))
    return os.path.join(base_path, filename)
# ============================================================
# MAIN APPLICATION
# ============================================================

def main():

    app = QApplication(sys.argv)
    app.setWindowIcon(QIcon(resource_path("logo.ico")))
    update_worker = None

    # ========================================================
    # Main Window
    # ========================================================

    window = QMainWindow()


    window.setWindowTitle(
        f"{APP_NAME} v{APP_VERSION}"
    )
    window.setWindowIcon(QIcon(resource_path("logo.ico")))

    window.setGeometry(
        500,
        200,
        500,
        200
    )

    # ========================================================
    # Central Widget
    # ========================================================

    central_widget = QWidget()

    window.setCentralWidget(
        central_widget
    )

    # ========================================================
    # Layout
    # ========================================================

    layout = QVBoxLayout()

    central_widget.setLayout(
        layout
    )

    # ========================================================
    # Header
    # ========================================================

    header_label = QLabel(
        "Remove Logo based on the client"
    )

    header_font = QFont()

    header_font.setPointSize(
        12
    )

    header_font.setBold(
        True
    )

    header_label.setFont(
        header_font
    )

    header_label.setAlignment(
        Qt.AlignmentFlag.AlignHCenter
    )

    layout.addWidget(
        header_label
    )

    # ========================================================
    # Filename Label
    # ========================================================

    filename_label = QLabel(
        "No File selected"
    )

    filename_font = QFont()

    filename_font.setPointSize(
        8
    )

    filename_label.setFont(
        filename_font
    )

    filename_label.setAlignment(
        Qt.AlignmentFlag.AlignHCenter
    )

    layout.addWidget(
        filename_label
    )

    # ========================================================
    # Upload Button
    # ========================================================

    upload_button = QPushButton(
        "Upload ZIP File"
    )

    layout.addWidget(
        upload_button,
        alignment=Qt.AlignmentFlag.AlignHCenter
    )

    # ========================================================
    # Store ZIP path
    # ========================================================

    zip_file_path = ""

    # ========================================================
    # Upload ZIP Function
    # ========================================================

    def upload_zip():

        nonlocal zip_file_path

        file_path, _ = QFileDialog.getOpenFileName(
            window,
            "Select ZIP File",
            "",
            "ZIP Files (*.zip)"
        )

        if not file_path:
            return

        zip_file_path = file_path

        filename_label.setText(
            os.path.basename(
                file_path
            )
        )

    upload_button.clicked.connect(
        upload_zip
    )

    # ========================================================
    # Client Dropdown
    # ========================================================

    dropdown = QComboBox()

    # Display name + internal value

    dropdown.addItem(
        "ASM Technologies Limited",
        "asm"
    )

    dropdown.addItem(
        "Anora Instruementation Pvt Ltd",
        "anora"
    )

    dropdown.addItem(
        "SANMINA Tech Services India Pvt Ltd",
        "sanmina"
    )

    dropdown.addItem(
        "SOLINAS Integrity Pvt Ltd",
        "solinas"
    )

    dropdown.addItem(
        "VVDN Technologies Pvt Ltd",
        "vvdn"
    )

    # Initially empty
    dropdown.setCurrentIndex(
        -1
    )

    dropdown.setPlaceholderText(
        "Select Client"
    )

    layout.addWidget(
        dropdown,
        alignment=Qt.AlignmentFlag.AlignHCenter
    )

    # ========================================================
    # Store selected client
    # ========================================================

    selected_client = ""

    # ========================================================
    # Client Selection
    # ========================================================

    def client_selected(index):

        nonlocal selected_client

        if index >= 0:

            selected_client = dropdown.currentData()

    dropdown.currentIndexChanged.connect(
        client_selected
    )

    # ========================================================
    # Process Button
    # ========================================================

    process_button = QPushButton(
        "Process"
    )

    layout.addWidget(
        process_button,
        alignment=Qt.AlignmentFlag.AlignHCenter
    )

    # ========================================================
    # Process Function
    # ========================================================

    def process_data():

        # ====================================================
        # Validate ZIP
        # ====================================================

        if not zip_file_path:

            filename_label.setText(
                "Please select a ZIP file."
            )

            return

        # ====================================================
        # Validate Client
        # ====================================================

        if not selected_client:

            filename_label.setText(
                "Please select a client."
            )

            return

        # ====================================================
        # Ask user where to save
        # ====================================================

        save_path, _ = QFileDialog.getSaveFileName(
            window,
            "Save Processed ZIP",
            "processed.zip",
            "ZIP Files (*.zip)"
        )

        # User cancelled
        if not save_path:

            filename_label.setText(
                "Save cancelled."
            )

            return

        # ====================================================
        # Make sure ZIP extension exists
        # ====================================================

        if not save_path.lower().endswith(
            ".zip"
        ):

            save_path += ".zip"

        # ====================================================
        # Disable button while processing
        # ====================================================

        process_button.setEnabled(
            False
        )

        process_button.setText(
            "Processing..."
        )

        filename_label.setText(
            "Processing ZIP..."
        )

        # ====================================================
        # Process ZIP
        # ====================================================

        try:

            process_zip(
                zip_file_path,
                selected_client,
                save_path
            )

            filename_label.setText(
                "File saved successfully."
            )

        except Exception as e:

            filename_label.setText(
                "Processing failed."
            )

            print(
                "Error:",
                str(e)
            )

        finally:

            process_button.setEnabled(
                True
            )

            process_button.setText(
                "Process"
            )

    process_button.clicked.connect(
        process_data
    )

    # ========================================================
    # Show Window
    # ========================================================

    window.show()

    def offer_update():
        """Check after the main window is visible, then offer the release."""
        release = check_for_update()
        if not release:
            return

        is_mandatory = bool(release.get("mandatory"))
        release_notes = release.get("release_notes") or "No release notes were provided."
        buttons = QMessageBox.StandardButton.Yes
        if not is_mandatory:
            buttons |= QMessageBox.StandardButton.No

        reply = QMessageBox.question(
            window,
            "Update Available",
            f"Version {release['version']} is available.\n\n"
            f"Current version: {APP_VERSION}\n\n"
            f"Release notes:\n{release_notes}\n\n"
            "Download and restart now?",
            buttons,
            QMessageBox.StandardButton.Yes,
        )

        if reply == QMessageBox.StandardButton.Yes:
            begin_update_download(release)

    def begin_update_download(release):
        nonlocal update_worker

        progress_dialog = QProgressDialog(
            "Preparing update download…", None, 0, 0, window
        )
        progress_dialog.setWindowTitle("Updating File Formatter")
        progress_dialog.setWindowModality(Qt.WindowModality.WindowModal)
        progress_dialog.setAutoClose(False)
        progress_dialog.setAutoReset(False)
        progress_dialog.setMinimumDuration(0)
        progress_dialog.show()

        update_worker = UpdateDownloadWorker(
            release["windows_download_url"],
            release["windows_sha256"],
            app,
        )

        def update_progress(percent):
            if progress_dialog.maximum() == 0:
                progress_dialog.setRange(0, 100)
            progress_dialog.setLabelText(f"Downloading update… {percent}%")
            progress_dialog.setValue(percent)

        def update_downloaded(update_file):
            nonlocal update_worker
            progress_dialog.setLabelText("Installing update and restarting…")
            progress_dialog.setRange(0, 0)

            if not start_updater(update_file):
                progress_dialog.close()
                try:
                    os.remove(update_file)
                except OSError:
                    pass
                QMessageBox.critical(
                    window,
                    "Update Failed",
                    "The update was downloaded, but the updater could not start."
                    " Please close the application and try again.",
                )
                update_worker = None
                return

            # The temporary updater waits for this process, swaps the EXE,
            # and starts the new version.  Exiting here releases the file lock.
            app.quit()

        def update_failed(message):
            nonlocal update_worker
            progress_dialog.close()
            QMessageBox.critical(window, "Update Failed", message)
            update_worker = None

        update_worker.progress_changed.connect(update_progress)
        update_worker.finished_successfully.connect(update_downloaded)
        update_worker.failed.connect(update_failed)
        update_worker.start()

    # Let Qt render the main window before the update check begins.
    QTimer.singleShot(0, offer_update)

    sys.exit(
        app.exec()
    )


# ============================================================
# ENTRY POINT
# ============================================================

if __name__ == "__main__":
    main()
