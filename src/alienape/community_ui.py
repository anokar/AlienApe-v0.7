"""Offline UI for explicitly exporting and inspecting unverified observations."""
import json
import unicodedata

from PyQt6.QtCore import Qt, QIODevice, QSaveFile
from PyQt6.QtWidgets import (
    QAbstractItemView, QCheckBox, QDialog, QFileDialog, QFormLayout, QHBoxLayout,
    QLabel, QLineEdit, QMessageBox, QPlainTextEdit, QPushButton, QSplitter,
    QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from .community_evidence import (
    MAX_CARD_BYTES, CardValidationError, CommunityEvidenceStore,
    export_local_observation, list_local_observations, serialize_card,
)


def _display_safe(value):
    """Make invisible Unicode controls visible without changing stored card data."""
    if isinstance(value, str):
        return "".join(
            f"⟦U+{ord(character):04X}⟧"
            if unicodedata.category(character) in {"Cc", "Cf", "Cs", "Co", "Cn", "Zl", "Zp"}
            else character
            for character in value
        )
    if isinstance(value, dict):
        return {_display_safe(key): _display_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_display_safe(item) for item in value]
    return value


class ExportObservationDialog(QDialog):
    def __init__(self, experience_store, local_id, parent=None):
        super().__init__(parent)
        self.experience_store = experience_store
        self.local_id = local_id
        self.card = None
        self.setWindowTitle("Community Card exportieren")
        self.resize(720, 680)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(
            "Prüfe und verallgemeinere die Beschreibung. Es werden keine Prompts, Antworten, "
            "Dateiinhalte, lokalen Pfade oder Endpunkte exportiert. Die Karte bleibt unverified."
        ))
        form = QFormLayout()
        self.description = QLineEdit("Beobachtung zu einer lokalen Aufgabe")
        self.description.setMaxLength(240)
        self.limitations = QLineEdit()
        self.limitations.setMaxLength(500)
        form.addRow("Allgemeine Beschreibung", self.description)
        form.addRow("Bekannte Einschränkungen", self.limitations)
        layout.addLayout(form)
        self.preview = QPlainTextEdit()
        self.preview.setReadOnly(True)
        self.preview.setPlaceholderText("Die privacy-geprüfte Exportvorschau erscheint hier.")
        layout.addWidget(self.preview, 1)
        self.confirm = QCheckBox("Ich habe die Vorschau geprüft und bestätige den Export dieser Karte.")
        layout.addWidget(self.confirm)
        buttons = QHBoxLayout()
        self.export_button = QPushButton("JSON speichern …")
        self.export_button.setEnabled(False)
        cancel = QPushButton("Abbrechen")
        buttons.addStretch()
        buttons.addWidget(cancel)
        buttons.addWidget(self.export_button)
        layout.addLayout(buttons)
        self.description.textChanged.connect(self.update_preview)
        self.limitations.textChanged.connect(self.update_preview)
        self.confirm.toggled.connect(self.update_enabled)
        self.export_button.clicked.connect(self.save_card)
        cancel.clicked.connect(self.reject)
        self.update_preview()

    def update_preview(self, *_):
        try:
            self.card = export_local_observation(
                self.experience_store, self.local_id,
                self.description.text(), self.limitations.text(),
            )
            self.preview.setPlainText(json.dumps(_display_safe(self.card), ensure_ascii=False, sort_keys=True, indent=2))
        except (CardValidationError, ValueError) as exc:
            self.card = None
            self.preview.setPlainText(f"Export nicht möglich: {exc}")
        self.update_enabled()

    def update_enabled(self, *_):
        self.export_button.setEnabled(self.card is not None and self.confirm.isChecked())

    def save_card(self):
        if self.card is None or not self.confirm.isChecked():
            return
        destination, _ = QFileDialog.getSaveFileName(
            self, "Community Card speichern", "community-card.json", "JSON (*.json)"
        )
        if not destination:
            return
        try:
            raw = serialize_card(self.card)
            output = QSaveFile(destination)
            if not output.open(QIODevice.OpenModeFlag.WriteOnly):
                raise OSError(output.errorString())
            if output.write(raw) != len(raw) or not output.commit():
                output.cancelWriting()
                raise OSError(output.errorString())
        except (OSError, CardValidationError) as exc:
            QMessageBox.warning(self, "Community Card", str(exc))
            return
        self.accept()


class CommunityCardsPanel(QWidget):
    def __init__(self, experience_store, community_store: CommunityEvidenceStore, parent=None):
        super().__init__(parent)
        self.experience_store = experience_store
        self.community_store = community_store
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(
            "Offline-Austausch einzelner, lokal beobachteter Ergebnisse. Importierte Karten sind "
            "unverified, verändern die lokale Experience nicht und beeinflussen keine Modellauswahl."
        ))
        self.splitter = QSplitter(Qt.Orientation.Vertical)
        layout.addWidget(self.splitter, 1)

        local_page = QWidget()
        local_layout = QVBoxLayout(local_page)
        local_layout.addWidget(QLabel("Lokale Beobachtungen · nur einzeln und nach Vorschau exportieren"))
        self.local_table = QTableWidget(0, 6)
        self.local_table.setHorizontalHeaderLabels(["Zeit", "Modell", "Kategorie", "Vertrag", "Quelle", "Aufgabenqualität"])
        self.local_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.local_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.local_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.local_table.horizontalHeader().setStretchLastSection(True)
        local_layout.addWidget(self.local_table, 1)
        actions = QHBoxLayout()
        self.export_button = QPushButton("Ausgewählte Beobachtung prüfen und exportieren …")
        self.export_button.setEnabled(False)
        refresh_local = QPushButton("Aktualisieren")
        actions.addWidget(refresh_local)
        actions.addStretch()
        actions.addWidget(self.export_button)
        local_layout.addLayout(actions)
        self.splitter.addWidget(local_page)

        cards_page = QWidget()
        cards_layout = QVBoxLayout(cards_page)
        card_actions = QHBoxLayout()
        card_actions.addWidget(QLabel("Importierte Community Cards · immer UNVERIFIED"))
        card_actions.addStretch()
        self.import_button = QPushButton("JSON importieren …")
        card_actions.addWidget(self.import_button)
        refresh_cards = QPushButton("Aktualisieren")
        card_actions.addWidget(refresh_cards)
        cards_layout.addLayout(card_actions)
        self.card_table = QTableWidget(0, 5)
        self.card_table.setHorizontalHeaderLabels(["Card ID", "Modell", "Kategorie", "Importiert", "Vertrauen"])
        self.card_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.card_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.card_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.card_table.horizontalHeader().setStretchLastSection(True)
        cards_layout.addWidget(self.card_table, 1)
        self.card_detail = QPlainTextEdit()
        self.card_detail.setReadOnly(True)
        self.card_detail.setPlaceholderText("Wähle eine importierte Karte aus, um ihre Felder anzusehen.")
        cards_layout.addWidget(self.card_detail, 1)
        self.splitter.addWidget(cards_page)

        refresh_local.clicked.connect(self.refresh_local)
        refresh_cards.clicked.connect(self.refresh_cards)
        self.export_button.clicked.connect(self.export_selected)
        self.import_button.clicked.connect(self.import_card)
        self.local_table.itemSelectionChanged.connect(self.update_export_enabled)
        self.card_table.itemSelectionChanged.connect(self.show_selected_card)
        self.refresh_local()
        self.refresh_cards()

    def refresh_local(self):
        self.local_table.setRowCount(0)
        try:
            observations = list_local_observations(self.experience_store)
        except Exception as exc:
            QMessageBox.warning(self, "Lokale Experience", str(exc))
            observations = []
        for observation in observations:
            row = self.local_table.rowCount()
            self.local_table.insertRow(row)
            values = [observation["observed_at"], observation["model"], observation["category"],
                      observation["contract"], observation["source"], observation["task_quality"]]
            for column, value in enumerate(values):
                item = QTableWidgetItem(str(value))
                if column == 0:
                    item.setData(Qt.ItemDataRole.UserRole, observation["local_id"])
                self.local_table.setItem(row, column, item)
        self.update_export_enabled()

    def update_export_enabled(self):
        self.export_button.setEnabled(self.local_table.currentRow() >= 0)

    def export_selected(self):
        row = self.local_table.currentRow()
        item = self.local_table.item(row, 0) if row >= 0 else None
        if item is None:
            return
        dialog = ExportObservationDialog(self.experience_store, item.data(Qt.ItemDataRole.UserRole), self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            QMessageBox.information(self, "Community Card", "Die Community Card wurde gespeichert. Der Import bleibt ein separater, manueller Schritt.")

    def import_card(self):
        filename, _ = QFileDialog.getOpenFileName(self, "Community Card importieren", "", "JSON (*.json)")
        if not filename:
            return
        try:
            with open(filename, "rb") as source:
                raw = source.read(MAX_CARD_BYTES + 1)
            result = self.community_store.import_json(raw)
            self.refresh_cards()
            QMessageBox.information(self, "Community Card", {
                "IMPORTED": "Karte importiert und als UNVERIFIED gespeichert.",
                "DUPLICATE": "Diese Karte ist bereits vorhanden.",
            }[result])
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "Community Card Import", str(exc))

    def refresh_cards(self):
        self.card_table.setRowCount(0)
        for card in self.community_store.list_cards():
            row = self.card_table.rowCount()
            self.card_table.insertRow(row)
            values = [card["card_id"], card["model_name"], card["category"], card["imported_at"], "UNVERIFIED"]
            for column, value in enumerate(values):
                item = QTableWidgetItem(str(value))
                if column == 0:
                    item.setData(Qt.ItemDataRole.UserRole, card["card_id"])
                self.card_table.setItem(row, column, item)
        self.card_detail.clear()

    def show_selected_card(self):
        row = self.card_table.currentRow()
        item = self.card_table.item(row, 0) if row >= 0 else None
        if item is None:
            self.card_detail.clear()
            return
        record = self.community_store.get_card(item.data(Qt.ItemDataRole.UserRole))
        if record:
            self.card_detail.setPlainText(json.dumps(_display_safe(record["card"]), ensure_ascii=False, sort_keys=True, indent=2))
