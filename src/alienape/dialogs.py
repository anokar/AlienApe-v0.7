from copy import deepcopy
from PyQt6.QtWidgets import (QDialog, QDialogButtonBox, QVBoxLayout, QHBoxLayout,
    QFormLayout, QLabel, QLineEdit, QPlainTextEdit, QComboBox, QSpinBox,
    QDoubleSpinBox, QCheckBox, QPushButton, QMessageBox, QScrollArea, QWidget, QToolButton)
from .backends import create_backend
from .memory import TYPES
from .i18n import localize


def accessible(widget, name, tip=""):
    widget.setAccessibleName(name)
    widget.setToolTip(tip or name)
    return widget


def button(text, action=None, tip=""):
    widget = accessible(QPushButton(text), text, tip)
    widget.setMinimumHeight(40)
    if action:
        widget.clicked.connect(action)
    return widget


class SettingsDialog(QDialog):
    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self.settings = deepcopy(settings)
        self.setWindowTitle("Einstellungen · Modelle und Bedienung")
        self.resize(660, 620)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.language=accessible(QComboBox(),localize('UI_LANGUAGE',settings.ui.language))
        for title,key in [('System','system'),('Deutsch','de'),('English','en'),('Français','fr')]:self.language.addItem(title,key)
        self.language.setCurrentIndex(self.language.findData(settings.ui.language))
        form.addRow(localize('UI_LANGUAGE',settings.ui.language),self.language)
        self.provider = accessible(QComboBox(), "Modellanbieter")
        for title, key in [("Demo (MockBackend, ohne KI)", "mock"), ("Ollama", "ollama"), ("LM Studio", "lmstudio")]:
            self.provider.addItem(title, key)
        self.provider.setCurrentIndex(self.provider.findData(settings.model.provider))
        self.url = accessible(QLineEdit(settings.model.base_url), "Lokale Server-URL")
        form.addRow("&Anbieter", self.provider)
        form.addRow("Server-&URL", self.url)
        self.url.setEnabled(settings.model.provider != "mock")
        self.provider.currentIndexChanged.connect(self.provider_changed)
        if settings.models:
            self.provider.setEnabled(False)
            self.url.setEnabled(False)
            self.provider.setToolTip("Anbieter und Server jetzt unter Modelle verwalten bearbeiten.")
            self.url.setToolTip("Anbieter und Server jetzt unter Modelle verwalten bearbeiten.")
        self.timeout = self.spin(1, 600, int(settings.model.timeout), "Server-Zeitlimit")
        self.steps = self.spin(1, 100, settings.agent.max_steps, "Maximale Agent-Schritte")
        self.budget = self.spin(2048, 1000000, settings.agent.context_budget, "Kontextbudget in UTF-8-Bytes")
        self.scale = accessible(QDoubleSpinBox(), "Schriftskalierung")
        self.scale.setRange(0.8, 2.0)
        self.scale.setSingleStep(0.1)
        self.scale.setValue(settings.ui.font_scale)
        self.density = accessible(QComboBox(), "Größe der Bedienelemente")
        self.density.addItems(["kompakt", "normal", "groß"])
        self.density.setCurrentText(settings.ui.density)
        for label, widget in [("Zeitlimit (Sekunden)", self.timeout), ("Maximale Schritte", self.steps), ("Kontextbudget (Bytes)", self.budget), ("Schriftskalierung", self.scale), ("Bedienelemente", self.density)]:
            form.addRow(label, widget)
        self.contrast = accessible(QCheckBox("Hoher Kontrast"), "Hoher Kontrast")
        self.contrast.setChecked(settings.ui.high_contrast)
        self.memory_on = accessible(QCheckBox("Projekt-Memory verwenden"), "Projekt-Memory verwenden")
        self.memory_on.setChecked(settings.memory.enabled)
        self.memory_limit = self.spin(1, 20, settings.memory.retrieval_limit, "Maximale Memory-Treffer")
        form.addRow(self.contrast)
        form.addRow(self.memory_on)
        form.addRow("Memory-Treffer", self.memory_limit)
        self.diagnostic_trace=accessible(QCheckBox('Diagnostic Trace (lokal)'), 'Diagnostic Trace', 'Optional: redaktierte Diagnose pro Aufgabe lokal speichern. Standardmäßig aus.')
        self.diagnostic_trace.setChecked(settings.diagnostic_trace)
        form.addRow('Debug',self.diagnostic_trace)
        from .semantic_ui import SemanticSettingsWidget
        self.semantic_widget=SemanticSettingsWidget(settings.semantic_resolver,self)
        self.semantic_toggle=QToolButton();self.semantic_toggle.setText('Erweitert: Semantic Intent Resolver');self.semantic_toggle.setCheckable(True)
        self.semantic_widget.setVisible(False);self.semantic_toggle.toggled.connect(self.semantic_widget.setVisible)
        form.addRow(self.semantic_toggle);form.addRow(self.semantic_widget)
        self.rules = {}
        self.verification_mode=accessible(QComboBox(),'Verification-Modus')
        for key in ['OFF','ASK','AUTO_SAFE']:self.verification_mode.addItem(key,key)
        self.verification_mode.setCurrentText(settings.verification.mode)
        self.verification_python=accessible(QLineEdit(settings.verification.python_executable),'Python-Verifier-Executable')
        self.verification_godot=accessible(QLineEdit(settings.verification.godot_executable),'Godot-Verifier-Executable')
        form.addRow('Verification',self.verification_mode)
        form.addRow('Python-Pfad (optional)',self.verification_python)
        form.addRow('Godot-Pfad (optional)',self.verification_godot)
        for key, title in [("write_file", "Dateien schreiben"), ("make_directory", "Ordner erstellen"),('verify_python_tests','Python-Tests ausführen'),('verify_godot','Godot-Prüfung ausführen')]:
            combo = accessible(QComboBox(), title)
            for label, value in [("Jedes Mal fragen", "ask"), ("Immer blockieren", "block"), ("Im Projekt automatisch erlauben", "auto")]:
                combo.addItem(label, value)
            combo.setCurrentIndex(combo.findData(settings.permissions.get(key, "ask")))
            form.addRow(title, combo)
            self.rules[key] = combo
        settings_body = QWidget()
        settings_body.setLayout(form)
        settings_scroll = QScrollArea()
        settings_scroll.setWidgetResizable(True)
        settings_scroll.setWidget(settings_body)
        layout.addWidget(settings_scroll, 1)
        note = QLabel("Nur lokale Server. Modelle werden nach dem Speichern abgerufen.\nProvider-Secrets werden im Modellprofil über Secret-Referenzen konfiguriert. Kontextbudget begrenzt Nachrichten; Tool-Definitionen und Modellantwort kommen hinzu.")
        note.setWordWrap(True)
        layout.addWidget(note)
        controls = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        controls.button(QDialogButtonBox.StandardButton.Save).setText("Speichern und Verbindung testen")
        controls.button(QDialogButtonBox.StandardButton.Cancel).setText("Abbrechen")
        controls.accepted.connect(self.save)
        controls.rejected.connect(self.reject)
        layout.addWidget(controls)

    def spin(self, lower, upper, value, name):
        widget = accessible(QSpinBox(), name)
        widget.setRange(lower, upper)
        widget.setValue(value)
        return widget

    def provider_changed(self):
        provider = self.provider.currentData()
        self.url.setEnabled(provider != "mock")
        if provider == "ollama":
            self.url.setText("http://localhost:11434/v1")
        elif provider == "lmstudio":
            self.url.setText("http://localhost:1234/v1")

    def save(self):
        s = self.settings
        if s.model.provider != self.provider.currentData():
            s.model.model = ""
        s.model.provider, s.model.base_url = self.provider.currentData(), self.url.text().strip()
        s.model.timeout = self.timeout.value()
        s.agent.max_steps, s.agent.context_budget = self.steps.value(), self.budget.value()
        s.ui.font_scale, s.ui.density = self.scale.value(), self.density.currentText()
        s.ui.high_contrast = self.contrast.isChecked()
        s.ui.language = self.language.currentData()
        s.memory.enabled, s.memory.retrieval_limit = self.memory_on.isChecked(), self.memory_limit.value()
        if self.semantic_widget.worker:return
        s.diagnostic_trace=self.diagnostic_trace.isChecked()
        s.semantic_resolver=self.semantic_widget.values()
        for key, widget in self.rules.items():
            s.permissions[key] = widget.currentData()
        s.verification.mode=self.verification_mode.currentData()
        s.verification.python_executable=self.verification_python.text().strip()
        s.verification.godot_executable=self.verification_godot.text().strip()
        try:
            s.validate()
            create_backend(s.model)
        except Exception as exc:
            QMessageBox.warning(self, "Einstellungen prüfen", str(exc))
            return
        self.accept()

    def reject(self):
        if self.semantic_widget.worker:
            self.semantic_widget.closing=True;self.semantic_widget.worker.cancel()
        else:super().reject()

    def closeEvent(self,event):
        if self.semantic_widget.worker:self.reject();event.ignore()
        else:event.accept()


class PermissionDialog(QDialog):
    decision = "deny"

    def __init__(self, request, parent=None):
        super().__init__(parent)
        language=getattr(getattr(getattr(parent,'settings',None),'ui',None),'language','system')
        verifying=request['name'] in {'verify_python_tests','verify_godot'}
        self.setWindowTitle(localize("PERMISSION_VERIFY_TITLE",language) if verifying else localize("PERMISSION_TITLE",language))
        self.resize(720, 540)
        layout = QVBoxLayout(self)
        heading = QLabel(localize("PERMISSION_VERIFY_HEADING" if verifying else "PERMISSION_WRITE_HEADING" if request["name"] == "write_file" else "PERMISSION_DIRECTORY_HEADING",language))
        heading.setWordWrap(True)
        layout.addWidget(heading)
        target = accessible(QLineEdit(request["path"]), "Zielpfad")
        target.setReadOnly(True)
        layout.addWidget(target)
        note = QLabel(localize("PERMISSION_ACTION",language,operation=request['name']).replace("\\n","\n"))
        note.setWordWrap(True)
        layout.addWidget(note)
        if request["name"] == "write_file":
            layout.addWidget(QLabel(localize("PERMISSION_PREVIEW",language)))
            preview = accessible(QPlainTextEdit(), "Vorschau des neuen Dateiinhalts")
            preview.setReadOnly(True)
            preview.setPlainText(request["arguments"].get("content", ""))
            layout.addWidget(preview)
        row = QHBoxLayout()
        row.addWidget(button(localize("ALLOW_ONCE",language), lambda: self.finish("once")))
        row.addWidget(button(localize("ALLOW_SESSION",language), lambda: self.finish("session")))
        deny = button(localize("DENY",language), lambda: self.finish("deny"))
        deny.setDefault(True)
        row.addWidget(deny)
        layout.addLayout(row)
        layout.addWidget(QLabel(localize("PERMISSION_ABORT",language)))

    def finish(self, decision):
        self.decision = decision
        self.accept()


class MemoryDialog(QDialog):
    def __init__(self, record=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Projektinformation speichern")
        self.resize(620, 400)
        record = record or {}
        layout = QFormLayout(self)
        self.key = accessible(QLineEdit(record.get("logical_key", "")), "Memory-Schlüssel", "Gleicher Schlüssel ersetzt die frühere Entscheidung.")
        self.kind = accessible(QComboBox(), "Memory-Typ")
        self.kind.addItems(TYPES)
        self.kind.setCurrentText(record.get("type", "USER_DECISION"))
        self.content = accessible(QPlainTextEdit(), "Memory-Inhalt")
        self.content.setPlainText(record.get("content", ""))
        layout.addRow("&Schlüssel", self.key)
        layout.addRow("&Typ", self.kind)
        layout.addRow("&Inhalt", self.content)
        controls = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        controls.button(QDialogButtonBox.StandardButton.Save).setText("Speichern")
        controls.button(QDialogButtonBox.StandardButton.Cancel).setText("Abbrechen")
        controls.accepted.connect(self.check)
        controls.rejected.connect(self.reject)
        layout.addRow(controls)

    def check(self):
        if not self.key.text().strip() or not self.content.toPlainText().strip():
            QMessageBox.warning(self, "Memory", "Bitte Schlüssel und Inhalt eingeben.")
            return
        self.accept()
