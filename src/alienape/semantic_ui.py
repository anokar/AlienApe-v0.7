"""Compact optional resolver settings. Model discovery is explicit, metadata-only."""
from PyQt6.QtWidgets import QWidget,QFormLayout,QCheckBox,QLineEdit,QComboBox,QLabel,QPushButton,QDoubleSpinBox
from .semantic_intent import SemanticSettings,OllamaEmbeddingBackend
from .worker import AgentWorker
from .i18n import localize

class TimeoutSpinBox(QDoubleSpinBox):
    def textFromValue(self,value):
        # Keep the compact native display while retaining supported precision.
        return self.locale().toString(value,'g',16)


class SemanticSettingsWidget(QWidget):
    def __init__(self,settings,parent=None):
        super().__init__(parent);self.owner=parent;self.worker=None;self.closing=False
        language=getattr(getattr(getattr(parent,'settings',None),'ui',None),'language','system')
        self.language=language
        form=QFormLayout(self);form.setContentsMargins(0,0,0,0);form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        self.enabled=QCheckBox(localize('SEMANTIC_ENABLE',language));self.enabled.setChecked(settings.enabled)
        self.url=QLineEdit(settings.base_url);self.model=QComboBox();self.model.setEditable(True);self.model.addItem(settings.model)
        # Retain the legacy value for settings-file compatibility; it does not affect resolver behavior.
        self.declared_multilingual=settings.declared_multilingual
        self.threshold=QDoubleSpinBox();self.threshold.setRange(0,1);self.threshold.setSingleStep(.05);self.threshold.setValue(settings.confidence_threshold)
        self.margin=QDoubleSpinBox();self.margin.setRange(0,1);self.margin.setSingleStep(.05);self.margin.setValue(settings.ambiguity_margin)
        self.initial_timeout=settings.timeout_seconds;self.timeout_changed=False
        self.timeout=TimeoutSpinBox();self.timeout.setDecimals(15);self.timeout.setRange(0.25,20.0)
        self.timeout.setSingleStep(1.0);self.timeout.setValue(settings.timeout_seconds)
        self.timeout.valueChanged.connect(self.mark_timeout_changed)
        self.saved_values=QLabel(localize('SEMANTIC_SAVED_VALUES',language,
            similarity=self.locale().toString(settings.confidence_threshold,'g',16),
            margin=self.locale().toString(settings.ambiguity_margin,'g',16),
            timeout=self.locale().toString(settings.timeout_seconds,'g',16)))
        self.saved_values.setWordWrap(True)
        self.reset=QPushButton(localize('SEMANTIC_RESET',language));self.reset.clicked.connect(self.reset_defaults)
        self.fetch=QPushButton(localize('SEMANTIC_FETCH',language));self.fetch.clicked.connect(self.discover)
        self.status=QLabel();self.status.setWordWrap(True)
        form.addRow(self.enabled)
        for message_id,widget in [('SEMANTIC_BACKEND',localize('SEMANTIC_LOCAL_ONLY',language)),('SEMANTIC_URL',self.url),('SEMANTIC_MODEL',self.model),('SEMANTIC_FETCH',self.fetch),('SEMANTIC_SIMILARITY',self.threshold),('SEMANTIC_MARGIN',self.margin),('SEMANTIC_TIMEOUT',self.timeout),('SEMANTIC_RESET',self.reset),('SEMANTIC_SAVED',self.saved_values),('SEMANTIC_STATUS',self.status)]:
            if isinstance(widget,str):widget=QLabel(widget)
            label=localize(message_id,language)
            widget.setAccessibleName(label or widget.text() if hasattr(widget,'text') else label)
            form.addRow(label,widget)
        self.model.currentTextChanged.connect(self.config_status);self.enabled.toggled.connect(self.config_status);self.config_status()

    def values(self):return SemanticSettings(enabled=self.enabled.isChecked(),base_url=self.url.text().strip(),model=self.model.currentText().strip(),confidence_threshold=self.threshold.value(),ambiguity_margin=self.margin.value(),timeout_seconds=self.timeout.value() if self.timeout_changed else self.initial_timeout,declared_multilingual=self.declared_multilingual)

    def reset_defaults(self):
        defaults=SemanticSettings()
        self.threshold.setValue(defaults.confidence_threshold)
        self.margin.setValue(defaults.ambiguity_margin)
        self.timeout.setValue(defaults.timeout_seconds)
        self.timeout_changed=True

    def mark_timeout_changed(self,_value):self.timeout_changed=True

    def config_status(self):
        self.status.setText(localize('SEMANTIC_DISABLED',self.language) if not self.enabled.isChecked() else localize('SEMANTIC_CONFIGURED',self.language) if self.model.currentText().strip() else localize('SEMANTIC_NOT_CONFIGURED',self.language))

    def discover(self):
        if self.worker:return
        try:backend=OllamaEmbeddingBackend(self.values())
        except Exception as exc:self.status.setText(str(exc));return
        self.worker=AgentWorker(backend=backend)
        self.worker.models_ready.connect(self.discovered);self.worker.failure.connect(self.status.setText);self.worker.finished.connect(self.done)
        self.fetch.setEnabled(False);self.status.setText(localize('SEMANTIC_PROBING',self.language));self.worker.start()

    def discovered(self,result):
        current=self.model.currentText();self.model.clear();self.model.addItems(result['models']);self.model.setCurrentText(current)
        self.status.setText(localize('SEMANTIC_MODEL_AVAILABLE',self.language) if current in result['models'] else localize('SEMANTIC_MODEL_UNAVAILABLE',self.language))

    def done(self):
        worker,self.worker=self.worker,None;self.fetch.setEnabled(True);worker.deleteLater()
        if self.closing:self.owner.reject()
