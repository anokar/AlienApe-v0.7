import sys
from PyQt6.QtWidgets import QApplication, QMessageBox
from .config import Settings, data_directory


def main():
    from .security import install_crash_redaction
    install_crash_redaction()
    app = QApplication(sys.argv)
    app.setApplicationName("AlienApe")
    app.setOrganizationName("AlienApe")
    directory = data_directory()
    directory.mkdir(parents=True, exist_ok=True)
    try:
        settings = Settings.load(directory / "config.toml")
    except Exception as exc:
        QMessageBox.warning(None, "Einstellungen", f"Einstellungen konnten nicht geladen werden: {exc}\nStandardwerte werden verwendet. Die Datei bleibt unverändert.")
        settings = Settings()
    from .ui import MainWindow
    window = MainWindow(settings, directory)
    window.show()
    return app.exec()
