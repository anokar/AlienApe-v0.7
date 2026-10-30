"""Compact optional resolver settings. Model discovery is explicit, metadata-only."""
from PyQt6.QtWidgets import QWidget,QFormLayout,QCheckBox,QLineEdit,QComboBox,QLabel,QPushButton,QDoubleSpinBox
from .semantic_intent import SemanticSettings,OllamaEmbeddingBackend
from .worker import AgentWorker

class TimeoutSpinBox(QDoubleSpinBox):
    def textFromValue(self,value):
        # Keep the compact native display while retaining supported precision.
        return self.locale().toString(value,'g',16)


class SemanticSettingsWidget(QWidget):
    def __init__(self,settings,parent=None):
        super().__init__(parent);self.owner=parent;self.worker=None;self.closing=False
        form=QFormLayout(self);form.setContentsMargins(0,0,0,0);form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        self.enabled=QCheckBox('Semantic Resolver aktivieren');self.enabled.setChecked(settings.enabled)
        self.url=QLineEdit(settings.base_url);self.model=QComboBox();self.model.setEditable(True);self.model.addItem(settings.model)
        self.multilingual=QCheckBox('Mehrsprachig deklariert');self.multilingual.setChecked(settings.declared_multilingual);self.multilingual.setToolTip("Benutzerdeklaration, kein Qualitätsnachweis und keine Dialektzusage.")
        self.threshold=QDoubleSpinBox();self.threshold.setRange(0,1);self.threshold.setSingleStep(.05);self.threshold.setValue(settings.confidence_threshold)
        self.margin=QDoubleSpinBox();self.margin.setRange(0,1);self.margin.setSingleStep(.05);self.margin.setValue(settings.ambiguity_margin)
        self.initial_timeout=settings.timeout_seconds;self.timeout_changed=False
        self.timeout=TimeoutSpinBox();self.timeout.setDecimals(15);self.timeout.setRange(0.25,20.0)
        self.timeout.setSingleStep(1.0);self.timeout.setValue(settings.timeout_seconds)
        self.timeout.valueChanged.connect(self.mark_timeout_changed)
        self.saved_values=QLabel('Gespeichert beim Öffnen: Ähnlichkeit '+self.locale().toString(settings.confidence_threshold,'g',16)+' · Abstand '+self.locale().toString(settings.ambiguity_margin,'g',16)+' · Timeout '+self.locale().toString(settings.timeout_seconds,'g',16)+' s')
        self.saved_values.setWordWrap(True)
        self.reset=QPushButton('Auf Standard zurücksetzen');self.reset.clicked.connect(self.reset_defaults)
        self.fetch=QPushButton('Embedding-Modelle prüfen');self.fetch.clicked.connect(self.discover)
        self.status=QLabel();self.status.setWordWrap(True)
        for title,widget in [('',self.enabled),('Backend','Ollama · nur lokal'),('Resolver-URL',self.url),('Embedding-Modell',self.model),('',self.fetch),('',self.multilingual),('Ähnlichkeit (heuristisch)',self.threshold),('Abstand (heuristisch)',self.margin),('Timeout (Sekunden)',self.timeout),('',self.reset),('',self.saved_values),('Status',self.status)]:
            if isinstance(widget,str):widget=QLabel(widget)
            widget.setAccessibleName(title or widget.text() if hasattr(widget,'text') else title)
            form.addRow(title,widget)
        self.model.currentTextChanged.connect(self.config_status);self.enabled.toggled.connect(self.config_status);self.config_status()

    def values(self):return SemanticSettings(enabled=self.enabled.isChecked(),base_url=self.url.text().strip(),model=self.model.currentText().strip(),confidence_threshold=self.threshold.value(),ambiguity_margin=self.margin.value(),timeout_seconds=self.timeout.value() if self.timeout_changed else self.initial_timeout,declared_multilingual=self.multilingual.isChecked())

    def reset_defaults(self):
        defaults=SemanticSettings()
        self.threshold.setValue(defaults.confidence_threshold)
        self.margin.setValue(defaults.ambiguity_margin)
        self.timeout.setValue(defaults.timeout_seconds)
        self.timeout_changed=True

    def mark_timeout_changed(self,_value):self.timeout_changed=True

    def config_status(self):
        self.status.setText('Ausgeschaltet · deterministische Auflösung' if not self.enabled.isChecked() else 'Konfiguriert · Verfügbarkeit wird bei Bedarf geprüft' if self.model.currentText().strip() else 'Not configured · kein Modell wird heruntergeladen')

    def discover(self):
        if self.worker:return
        try:backend=OllamaEmbeddingBackend(self.values())
        except Exception as exc:self.status.setText(str(exc));return
        self.worker=AgentWorker(backend=backend)
        self.worker.models_ready.connect(self.discovered);self.worker.failure.connect(self.status.setText);self.worker.finished.connect(self.done)
        self.fetch.setEnabled(False);self.status.setText('Prüfe lokale Modellmetadaten …');self.worker.start()

    def discovered(self,result):
        current=self.model.currentText();self.model.clear();self.model.addItems(result['models']);self.model.setCurrentText(current)
        self.status.setText('Ready · konfiguriertes Modell meldet Embedding-Unterstützung' if current in result['models'] else 'Semantic resolver model is not available. Bitte ein installiertes Embedding-Modell auswählen.')

    def done(self):
        worker,self.worker=self.worker,None;self.fetch.setEnabled(True);worker.deleteLater()
        if self.closing:self.owner.reject()
