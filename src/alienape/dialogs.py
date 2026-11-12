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
        self.setWindowTitle(localize("DIALOG_SETTINGS_TITLE",settings.ui.language))
        self.resize(660, 620)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.language=accessible(QComboBox(),localize('UI_LANGUAGE',settings.ui.language))
        for title,key in [(localize('DIALOG_LANGUAGE_SYSTEM',settings.ui.language),'system'),(localize('DIALOG_LANGUAGE_DE',settings.ui.language),'de'),(localize('DIALOG_LANGUAGE_EN',settings.ui.language),'en'),(localize('DIALOG_LANGUAGE_FR',settings.ui.language),'fr')]:self.language.addItem(title,key)
        self.language.setCurrentIndex(self.language.findData(settings.ui.language))
        form.addRow(localize('UI_LANGUAGE',settings.ui.language),self.language)
        self.provider = accessible(QComboBox(), localize("DIALOG_PROVIDER",settings.ui.language))
        for title, key in [(localize("DIALOG_PROVIDER_MOCK",settings.ui.language), "mock"), ("Ollama", "ollama"), ("LM Studio", "lmstudio")]:
            self.provider.addItem(title, key)
        self.provider.setCurrentIndex(self.provider.findData(settings.model.provider))
        self.url = accessible(QLineEdit(settings.model.base_url), "Lokale Server-URL")
        form.addRow(localize("DIALOG_PROVIDER",settings.ui.language), self.provider)
        form.addRow(localize("DIALOG_SERVER_URL",settings.ui.language), self.url)
        self.url.setEnabled(settings.model.provider != "mock")
        self.provider.currentIndexChanged.connect(self.provider_changed)
        if settings.models:
            self.provider.setEnabled(False)
            self.url.setEnabled(False)
            self.provider.setToolTip(localize("DIALOG_PROVIDER_MANAGED",settings.ui.language))
            self.url.setToolTip(localize("DIALOG_PROVIDER_MANAGED",settings.ui.language))
        self.timeout = self.spin(1, 600, int(settings.model.timeout), localize("DIALOG_TIMEOUT",settings.ui.language))
        self.steps = self.spin(1, 100, settings.agent.max_steps, localize("DIALOG_MAX_STEPS",settings.ui.language))
        self.budget = self.spin(2048, 1000000, settings.agent.context_budget, localize("DIALOG_CONTEXT_BUDGET",settings.ui.language))
        self.scale = accessible(QDoubleSpinBox(), localize("DIALOG_FONT_SCALE",settings.ui.language))
        self.scale.setRange(0.8, 2.0)
        self.scale.setSingleStep(0.1)
        self.scale.setValue(settings.ui.font_scale)
        self.density = accessible(QComboBox(), localize("DIALOG_CONTROL_SIZE",settings.ui.language))
        density_values=[(localize("DIALOG_DENSITY_COMPACT",settings.ui.language),"kompakt"),(localize("DIALOG_DENSITY_NORMAL",settings.ui.language),"normal"),(localize("DIALOG_DENSITY_LARGE",settings.ui.language),"groß")]
        for label,value in density_values:self.density.addItem(label,value)
        self.density.setCurrentIndex(self.density.findData(settings.ui.density))
        for label, widget in [(localize("DIALOG_TIMEOUT_LABEL",settings.ui.language), self.timeout), (localize("DIALOG_MAX_STEPS",settings.ui.language), self.steps), (localize("DIALOG_CONTEXT_BUDGET_LABEL",settings.ui.language), self.budget), (localize("DIALOG_FONT_SCALE",settings.ui.language), self.scale), (localize("DIALOG_CONTROL_SIZE",settings.ui.language), self.density)]:
            form.addRow(label, widget)
        self.contrast = accessible(QCheckBox(localize("DIALOG_HIGH_CONTRAST",settings.ui.language)), localize("DIALOG_HIGH_CONTRAST",settings.ui.language))
        self.contrast.setChecked(settings.ui.high_contrast)
        self.memory_on = accessible(QCheckBox(localize("DIALOG_MEMORY_ENABLED",settings.ui.language)), localize("DIALOG_MEMORY_ENABLED",settings.ui.language))
        self.memory_on.setChecked(settings.memory.enabled)
        self.memory_limit = self.spin(1, 20, settings.memory.retrieval_limit, localize("DIALOG_MEMORY_LIMIT",settings.ui.language))
        form.addRow(self.contrast)
        form.addRow(self.memory_on)
        form.addRow(localize("DIALOG_MEMORY_LIMIT",settings.ui.language), self.memory_limit)
        self.diagnostic_trace=accessible(QCheckBox(localize('DIALOG_DIAGNOSTIC_TRACE',settings.ui.language)), localize('DIALOG_DIAGNOSTIC_TRACE',settings.ui.language), localize('DIALOG_DIAGNOSTIC_TIP',settings.ui.language))
        self.diagnostic_trace.setChecked(settings.diagnostic_trace)
        form.addRow(localize('DIALOG_DEBUG',settings.ui.language),self.diagnostic_trace)
        from .semantic_ui import SemanticSettingsWidget
        self.semantic_widget=SemanticSettingsWidget(settings.semantic_resolver,self)
        self.semantic_toggle=QToolButton();self.semantic_toggle.setText(localize('DIALOG_ADVANCED_SEMANTIC',settings.ui.language));self.semantic_toggle.setCheckable(True)
        self.semantic_widget.setVisible(False);self.semantic_toggle.toggled.connect(self.semantic_widget.setVisible)
        form.addRow(self.semantic_toggle);form.addRow(self.semantic_widget)
        self.rules = {}
        self.verification_mode=accessible(QComboBox(),localize('DIALOG_VERIFICATION_MODE',settings.ui.language))
        for key in ['OFF','ASK','AUTO_SAFE']:self.verification_mode.addItem(key,key)
        self.verification_mode.setCurrentText(settings.verification.mode)
        self.verification_python=accessible(QLineEdit(settings.verification.python_executable),localize('DIALOG_PYTHON_PATH',settings.ui.language))
        self.verification_godot=accessible(QLineEdit(settings.verification.godot_executable),localize('DIALOG_GODOT_PATH',settings.ui.language))
        form.addRow(localize('DIALOG_VERIFICATION_MODE',settings.ui.language),self.verification_mode)
        form.addRow(localize('DIALOG_PYTHON_PATH',settings.ui.language),self.verification_python)
        form.addRow(localize('DIALOG_GODOT_PATH',settings.ui.language),self.verification_godot)
        for key, message_id in [("write_file", "DIALOG_WRITE_FILES"), ("make_directory", "DIALOG_CREATE_FOLDERS"),('verify_python_tests','DIALOG_RUN_PYTHON_TESTS'),('verify_godot','DIALOG_RUN_GODOT_CHECK')]:
            title=localize(message_id,settings.ui.language)
            combo = accessible(QComboBox(), title)
            for label, value in [(localize("DIALOG_PERMISSION_ASK",settings.ui.language), "ask"), (localize("DIALOG_PERMISSION_BLOCK",settings.ui.language), "block"), (localize("DIALOG_PERMISSION_AUTO",settings.ui.language), "auto")]:
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
        note = QLabel(localize("DIALOG_SETTINGS_NOTE",settings.ui.language))
        note.setWordWrap(True)
        layout.addWidget(note)
        controls = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        controls.button(QDialogButtonBox.StandardButton.Save).setText(localize("DIALOG_SAVE_TEST",settings.ui.language))
        controls.button(QDialogButtonBox.StandardButton.Cancel).setText(localize("DIALOG_CANCEL",settings.ui.language))
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
        s.ui.font_scale, s.ui.density = self.scale.value(), self.density.currentData()
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
            QMessageBox.warning(self, localize("DIALOG_CHECK_SETTINGS",s.ui.language), str(exc))
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
        language=getattr(getattr(getattr(parent,'settings',None),'ui',None),'language','system')
        self.setWindowTitle(localize("DIALOG_SAVE_MEMORY_TITLE",language))
        self.resize(620, 400)
        record = record or {}
        layout = QFormLayout(self)
        self.key = accessible(QLineEdit(record.get("logical_key", "")), localize("DIALOG_MEMORY_KEY",language), localize("DIALOG_MEMORY_KEY_TIP",language))
        self.kind = accessible(QComboBox(), localize("DIALOG_MEMORY_TYPE",language))
        self.kind.addItems(TYPES)
        self.kind.setCurrentText(record.get("type", "USER_DECISION"))
        self.content = accessible(QPlainTextEdit(), localize("DIALOG_MEMORY_CONTENT",language))
        self.content.setPlainText(record.get("content", ""))
        layout.addRow(localize("DIALOG_KEY",language), self.key)
        layout.addRow(localize("DIALOG_TYPE",language), self.kind)
        layout.addRow(localize("DIALOG_CONTENT",language), self.content)
        controls = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        controls.button(QDialogButtonBox.StandardButton.Save).setText(localize("DIALOG_SAVE",language))
        controls.button(QDialogButtonBox.StandardButton.Cancel).setText(localize("DIALOG_CANCEL",language))
        controls.accepted.connect(self.check)
        controls.rejected.connect(self.reject)
        layout.addRow(controls)

    def check(self):
        if not self.key.text().strip() or not self.content.toPlainText().strip():
            language=getattr(getattr(getattr(self.parent(),'settings',None),'ui',None),'language','system')
            QMessageBox.warning(self, localize("DIALOG_MEMORY_TITLE",language), localize("DIALOG_MEMORY_MISSING",language))
            return
        self.accept()
