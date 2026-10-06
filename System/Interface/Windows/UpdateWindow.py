from __future__ import annotations

import os
import re
import sys
import json
import platform
import tempfile
import webbrowser
import subprocess

from loguru import logger

from PyQt6.QtCore import (
    Qt,
    QUrl,
    QTimer,
    pyqtSignal
)

from PyQt6.QtWidgets import (
    QWidget,
    QHBoxLayout,
    QVBoxLayout,
    QApplication
)

from PyQt6.QtNetwork import (
    QNetworkReply,
    QNetworkRequest,
    QNetworkAccessManager
)

from System.Common import (
    Utils,
    Styles,
    Constants
)

from System.Interface import Widgets
from System.Interface.Windows import FloatingWindowGPU

# Update Window

class UpdateWindow(FloatingWindowGPU):
    download_progress_updated = pyqtSignal(float, float)

    def __init__(
            self,
            version:             str,
            changelog:           str,
            url:                 str                       = Constants.GITHUB_LINK,
            release_information: dict[str, object] | None  = None,
            parent:              QWidget | None            = None
        ) -> None:

        super().__init__(f"Cassette {version}", parent = parent, enable_audioplayer_effects = False)

        self.version_text        = version
        self.changelog_text      = changelog
        self.release_url         = url
        self.release_information = release_information

        self.download_target_url = ""
        self.download_file_path  = ""
        self.temporary_file      = None
        self.download_reply      = None

        self.total_bytes         = 0
        self.received_bytes      = 0
        self.is_downloading      = False
        self.is_ready_to_install = False

        self.network_manager     = QNetworkAccessManager(self)

        self.setup_user_interface()
        self.resolve_target_asset()

    # Setup Layout

    def setup_user_interface(self) -> None:
        formatted_changelog = self.format_changelog_text(self.changelog_text)

        self.update_label = Widgets.DescriptionLabel(f"`A new version is available.`\n{formatted_changelog}", 700)

        self.scroll_area  = Widgets.ElasticScrollArea(self, Styles.Colors.Floating.Background)
        self.scroll_area.setFixedSize(700, 320)
        self.scroll_area.add_widget(self.update_label)

        self.status_label = Widgets.DescriptionLabel("", 700)
        self.status_label.setVisible(False)

        self.progress_bar = Widgets.TutorialProgressBar(self)
        self.progress_bar.setVisible(False)

        self.github_button = Widgets.ButtonWithOutline("View on GitHub")
        self.cancel_button = Widgets.ButtonWithOutline("Later")
        self.action_button = Widgets.NothingButton("Update now")

        self.github_button.clicked.connect(self.open_github_release)
        self.cancel_button.clicked.connect(self.handle_cancel_clicked)
        self.action_button.clicked.connect(self.handle_action_clicked)

        self.download_progress_updated.connect(self.update_progress_display)

        button_row_layout = QHBoxLayout()
        button_row_layout.addWidget(self.github_button)
        button_row_layout.addWidget(self.cancel_button)
        button_row_layout.addWidget(self.action_button)

        self.content_layout.addWidget(self.scroll_area)
        self.content_layout.addWidget(self.status_label)
        self.content_layout.addWidget(self.progress_bar)
        self.content_layout.addLayout(button_row_layout)

    # Version And Asset Resolution

    def resolve_target_asset(self) -> None:
        if not self.release_information:
            return

        assets_list = self.release_information.get("assets", [])

        if not isinstance(assets_list, list):
            return

        compatible_asset = self.find_compatible_asset(assets_list)

        if not compatible_asset:
            logger.warning("No compatible asset found for current platform and architecture.")
            self.action_button.setEnabled(False)

            return

        self.download_target_url = str(compatible_asset.get("browser_download_url", ""))
        download_size_bytes      = int(compatible_asset.get("size", 0))

        if download_size_bytes > 0:
            size_in_megabytes = download_size_bytes / (1024.0 * 1024.0)
            self.action_button.setText(f"Update now ({size_in_megabytes:.1f} MB)")

    @staticmethod
    def get_platform_asset_filename() -> str:
        platform_name = sys.platform
        machine_name  = platform.machine().lower()
        is_arm        = "arm" in machine_name or "aarch64" in machine_name

        if platform_name == "win32":
            architecture = "arm64" if is_arm else "x64"

            return f"cassette-windows-{architecture}.zip"

        if platform_name == "darwin":
            architecture = "silicon" if is_arm else "intel"

            return f"cassette-macos-{architecture}.zip"

        architecture = "arm64" if is_arm else "x64"

        return f"cassette-linux-{architecture}.zip"

    def find_compatible_asset(
            self,
            assets: list[dict[str, object]]
        ) -> dict[str, object] | None:

        target_name = self.get_platform_asset_filename()

        for asset in assets:
            asset_filename = str(asset.get("name", "")).lower()

            if asset_filename == target_name:
                return asset

        return None

    # Network Operations

    def start_download(self) -> None:
        if not self.download_target_url:
            self.open_github_release()

            return

        self.is_downloading = True

        self.action_button.setEnabled(False)
        self.github_button.setEnabled(False)
        self.cancel_button.setText("Cancel")

        self.status_label.setText("Connecting to server...")
        self.status_label.setVisible(True)

        self.progress_bar.set_total(100.0)
        self.progress_bar.set_completed(0.0)
        self.progress_bar.setVisible(True)

        target_file_name        = self.download_target_url.split("/")[-1]
        temporary_directory     = tempfile.gettempdir()
        self.download_file_path = os.path.join(temporary_directory, target_file_name)

        try:
            self.temporary_file = open(self.download_file_path, "wb")

        except OSError as exception:
            logger.error(f"Failed to create temporary update file: {exception}")
            self.handle_download_failure("Failed to create temporary file.")

            return

        request = QNetworkRequest(QUrl(self.download_target_url))
        request.setAttribute(
            QNetworkRequest.Attribute.RedirectPolicyAttribute,
            QNetworkRequest.RedirectPolicy.NoLessSafeRedirectPolicy
        )
        request.setRawHeader(b"User-Agent", b"Cassette-Updater")

        self.download_reply = self.network_manager.get(request)
        self.download_reply.downloadProgress.connect(self.handle_download_progress)
        self.download_reply.readyRead.connect(self.handle_data_received)
        self.download_reply.finished.connect(self.handle_download_finished)

    def handle_data_received(self) -> None:
        if not self.download_reply or not self.temporary_file:
            return

        chunk_data = self.download_reply.readAll()

        if chunk_data.isEmpty():
            return

        self.temporary_file.write(chunk_data.data())

    def handle_download_progress(
            self,
            received_bytes: int,
            total_bytes:    int
        ) -> None:

        if total_bytes <= 0:
            return

        self.received_bytes = received_bytes
        self.total_bytes    = total_bytes

        self.download_progress_updated.emit(float(received_bytes), float(total_bytes))

    def update_progress_display(
            self,
            received_bytes: float,
            total_bytes:    float
        ) -> None:

        received_megabytes = received_bytes / (1024.0 * 1024.0)
        total_megabytes    = total_bytes / (1024.0 * 1024.0)
        progress_percent   = (received_bytes / total_bytes) * 100.0

        self.progress_bar.set_total(total_megabytes)
        self.progress_bar.set_completed(received_megabytes)

        self.status_label.setText(
            f"Downloading: {received_megabytes:.1f} MB / {total_megabytes:.1f} MB ({progress_percent:.0f}%)"
        )

    def handle_download_finished(self) -> None:
        if self.temporary_file:
            self.temporary_file.close()
            self.temporary_file = None

        if not self.download_reply:
            return

        network_error = self.download_reply.error()
        self.download_reply.deleteLater()
        self.download_reply = None

        if network_error != QNetworkReply.NetworkError.NoError:
            self.handle_download_failure("Connection lost during download.")

            return

        self.is_downloading      = False
        self.is_ready_to_install = True

        self.status_label.setText("Update package downloaded. Click restart to apply.")
        self.action_button.setText("Restart now")
        self.action_button.setEnabled(True)
        self.cancel_button.setText("Later")
        self.github_button.setEnabled(True)

    def handle_download_failure(self, error_message: str) -> None:
        self.is_downloading = False

        self.clean_up_temporary_file()

        self.status_label.setText(f"Download failed: {error_message}")
        self.action_button.setText("Retry")
        self.action_button.setEnabled(True)
        self.cancel_button.setText("Close")
        self.github_button.setEnabled(True)

    def cancel_download(self) -> None:
        if self.download_reply:
            self.download_reply.abort()
            self.download_reply = None

        if self.temporary_file:
            self.temporary_file.close()
            self.temporary_file = None

        self.clean_up_temporary_file()

        self.is_downloading = False

        self.status_label.setVisible(False)
        self.progress_bar.setVisible(False)

        self.action_button.setText("Update now")
        self.action_button.setEnabled(True)
        self.cancel_button.setText("Later")
        self.github_button.setEnabled(True)

    def clean_up_temporary_file(self) -> None:
        if not self.download_file_path:
            return

        if not os.path.exists(self.download_file_path):
            return

        try:
            os.remove(self.download_file_path)

        except OSError:
            pass

    # Installation Hand Off

    def apply_update_and_restart(self) -> None:
        if not getattr(sys, "frozen", False):
            logger.warning("Running from Python source code. Binary replacement skipped.")
            self.on_ok()

            return

        executable_path    = sys.executable
        process_identifier = os.getpid()
        temporary_folder   = tempfile.gettempdir()

        if sys.platform == "win32":
            application_directory = os.path.dirname(executable_path)
            script_path           = os.path.join(temporary_folder, "cassette_updater.bat")

            script_content = f"""@echo off
chcp 65001 > nul
:wait_process
tasklist /fi "PID eq {process_identifier}" | find ":" > nul
if errorlevel 1 (
    timeout /t 1 /nobreak > nul
    goto wait_process
)
tar -xf "{self.download_file_path}" -C "{application_directory}" || powershell -Command "Expand-Archive -Force -Path '{self.download_file_path}' -DestinationPath '{application_directory}'"
start "" "{executable_path}"
del "{self.download_file_path}"
del "%~f0"
"""
            with open(script_path, "w", encoding = "utf-8") as script_file:
                script_file.write(script_content)

            subprocess.Popen(
                ["cmd.exe", "/c", script_path],
                creationflags = subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS
            )

        elif sys.platform == "darwin":
            bundle_directory = os.path.abspath(os.path.join(os.path.dirname(executable_path), "../../.."))
            parent_directory = os.path.dirname(bundle_directory)
            script_path      = os.path.join(temporary_folder, "cassette_updater.sh")

            script_content = f"""#!/bin/sh
while kill -0 {process_identifier} 2>/dev/null; do
    sleep 0.5
done
rm -rf "{bundle_directory}"
unzip -o "{self.download_file_path}" -d "{parent_directory}"
xattr -cr "{bundle_directory}"
open "{bundle_directory}"
rm -f "{self.download_file_path}"
rm -f "$0"
"""
            with open(script_path, "w", encoding = "utf-8") as script_file:
                script_file.write(script_content)

            os.chmod(script_path, 0o755)
            subprocess.Popen(["/bin/sh", script_path], start_new_session = True)

        else:
            application_directory = os.path.dirname(executable_path)
            script_path           = os.path.join(temporary_folder, "cassette_updater.sh")

            script_content = f"""#!/bin/sh
while kill -0 {process_identifier} 2>/dev/null; do
    sleep 0.5
done
unzip -o "{self.download_file_path}" -d "{application_directory}"
chmod +x "{executable_path}"
"{executable_path}" &
rm -f "{self.download_file_path}"
rm -f "$0"
"""
            with open(script_path, "w", encoding = "utf-8") as script_file:
                script_file.write(script_content)

            os.chmod(script_path, 0o755)
            subprocess.Popen(["/bin/sh", script_path], start_new_session = True)

        application = QApplication.instance()

        if application:
            application.quit()

        sys.exit(0)

    # Event Handlers

    def handle_action_clicked(self) -> None:
        if self.is_ready_to_install:
            self.apply_update_and_restart()

            return

        self.start_download()

    def handle_cancel_clicked(self) -> None:
        if self.is_downloading:
            self.cancel_download()

            return

        self.on_cancel()

    def open_github_release(self) -> None:
        webbrowser.open(self.release_url)

    def really_close(self) -> None:
        if self.is_downloading:
            self.cancel_download()

        super().really_close()

    # Text Formatting

    @staticmethod
    def format_changelog_text(changelog: str) -> str:
        changelog = re.sub(r"(?m)^### \*\*Cassette v\d+\.\d+\.\d+\*\*\s*\r?\n", "",      changelog)
        changelog = re.sub(r"(?m)^>\s*(.*?)\s*$",                               r"`\1`", changelog)
        changelog = re.sub(r"### \*\*(.*?)\*\*",                                r"`\1`", changelog)
        changelog = re.sub(r"\*\*(.*?)\*\*",                                    r"`\1`", changelog)
        changelog = re.sub(r"(?m)^-\s+",                                        "`•` ",  changelog)

        return changelog