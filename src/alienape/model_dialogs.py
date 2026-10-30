"""Small profile editor; uses the existing async backend probe and Qt workers."""
from copy import deepcopy
from PyQt6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QFormLayout, QLabel,
    QLineEdit, QComboBox, QCheckBox, QSpinBox, QTableWidget, QTableWidgetItem,
    QAbstractItemView, QDialogButtonBox, QMessageBox, QTabWidget, QWidget, QPlainTextEdit)
from .dialogs import accessible, button
from .profiles import ModelProfile
from .routing import ModelRegistry
from .config import ModelSettings
from .backends import create_backend
from .worker import AgentWorker


class ProfileDialog(QDialog):
    def __init__(self, profile, timeout=120, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Modellprofil bearbeiten")
        self.resize(800, 740)
        self.profile, self.timeout = deepcopy(profile), timeout
        self.worker = None
        self.close_after_probe = False
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.identifier = accessible(QLineEdit(profile.id), "Eindeutige Modell-ID")
        self.display_name = accessible(QLineEdit(profile.display_name), "Anzeigename des Modells")
        self.provider = accessible(QComboBox(), "Anbieter des Modellprofils")
        self.provider.addItems(["ollama", "lmstudio", "mock"])
        self.provider.setCurrentText(profile.provider)
        self.url = accessible(QLineEdit(profile.base_url), "Server-URL des Modellprofils")
        self.secret_reference = accessible(QLineEdit(profile.secret_reference), "Provider-Secret-Referenz")
        self.secret_value = accessible(QLineEdit(), "Neues Provider-Secret")
        self.secret_value.setEchoMode(QLineEdit.EchoMode.Password)
        self.secret_value.setPlaceholderText("Configured ✓" if profile.secret_reference else "Optional; Windows Credential Manager")
        self.model_name = accessible(QComboBox(), "Modellname beim Server")
        self.model_name.setEditable(True)
        self.model_name.addItem(profile.model_name)
        self.probe_button = button("Modelle vom Server abrufen", self.probe)
        self.role = accessible(QComboBox(), "Rolle des Modells")
        self.role.addItems(["general", "coding", "reasoning"])
        self.role.setCurrentText(profile.role)
        self.capabilities = accessible(QLineEdit(", ".join(profile.capabilities)), "Konfigurierte Fähigkeiten", "Kommagetrennt, z. B. coding, debugging, code_review. Keine Ableitung aus dem Modellnamen.")
        self.tool_mode = accessible(QComboBox(), "Werkzeugmodus des Modells")
        self.tool_mode.addItems(["native", "none", "json_fallback"])
        self.tool_mode.setCurrentText(profile.tool_mode)
        self.speed = accessible(QComboBox(), "Geschwindigkeitsklasse")
        self.speed.addItems(["fast", "normal", "slow"])
        self.speed.setCurrentText(profile.speed_class)
        self.resource = accessible(QComboBox(), "Ressourcenklasse")
        self.resource.addItems(["low", "medium", "high"])
        self.resource.setCurrentText(profile.resource_class)
        self.priority = accessible(QSpinBox(), "Modellpriorität bei gleicher Ressourcenklasse")
        self.priority.setRange(-1000, 1000)
        self.priority.setValue(profile.priority)
        self.context = accessible(QSpinBox(), "Bekannte Kontextlänge in Tokens")
        self.context.setRange(0, 10000000)
        self.context.setSpecialValueText("Unbekannt")
        self.context.setValue(profile.context_length)
        self.enabled = accessible(QCheckBox("Profil aktivieren"), "Modellprofil aktivieren")
        self.enabled.setChecked(profile.enabled)
        for title, widget in [("ID", self.identifier), ("Name", self.display_name), ("Anbieter", self.provider),
            ("Lokale Server-URL", self.url), ("Secret-Referenz (env:NAME möglich)", self.secret_reference), ("Neues Secret (maskiert)", self.secret_value), ("Modell beim Server", self.model_name), ("", self.probe_button),
            ("Rolle", self.role), ("Fähigkeiten (konfiguriert)", self.capabilities), ("Werkzeuge", self.tool_mode),
            ("Geschwindigkeit", self.speed), ("Ressourcen", self.resource), ("Priorität", self.priority),
            ("Kontextlänge (0 = unbekannt)", self.context), ("", self.enabled)]:
            form.addRow(title, widget)
        from .profile_v2 import TASKS,PROTOCOLS,Support
        self.v2=profile.v2()
        self.profile_tabs=QTabWidget()
        overview=QWidget();overview_layout=QVBoxLayout(overview);overview_layout.addLayout(form)
        self.profile_tabs.addTab(overview,'Übersicht')
        caps_page=QWidget();caps_layout=QVBoxLayout(caps_page)
        caps_layout.addWidget(QLabel('Deklaration, keine verifizierte Leistung. UNKNOWN ist konservativ.'))
        self.v2_table=QTableWidget(len(TASKS)+len(PROTOCOLS),3)
        self.v2_table.setHorizontalHeaderLabels(['Dimension','Fähigkeit','Deklariert'])
        self.v2_selectors={}
        for row,(dimension,name) in enumerate([(d,n) for d,ns in [('task_capabilities',TASKS),('protocol_capabilities',PROTOCOLS)] for n in ns]):
            self.v2_table.setItem(row,0,QTableWidgetItem('Aufgabe' if dimension=='task_capabilities' else 'Protokoll'))
            self.v2_table.setItem(row,1,QTableWidgetItem(name))
            combo=accessible(QComboBox(),f'{dimension} {name}')
            combo.addItems(list(Support._value2member_map_));combo.setCurrentText(getattr(self.v2,dimension)[name])
            self.v2_table.setCellWidget(row,2,combo);self.v2_selectors[dimension,name]=combo
        self.v2_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.v2_table.horizontalHeader().setStretchLastSection(True);self.v2_table.resizeColumnsToContents()
        caps_layout.addWidget(self.v2_table)
        language_form=QFormLayout()
        self.languages=accessible(QLineEdit(', '.join(self.v2.languages.get('declared_languages',[]))),'Deklarierte Sprachen')
        self.multilingual=accessible(QComboBox(),'Mehrsprachigkeit deklariert');self.multilingual.addItems(list(Support._value2member_map_));self.multilingual.setCurrentText(self.v2.languages.get('declared_multilingual','UNKNOWN'))
        self.primary_language=accessible(QLineEdit(self.v2.languages.get('primary_language','de')),'Primäre Testsprache')
        self.reasoning_supported=accessible(QComboBox(),'Reasoning deklariert');self.reasoning_supported.addItems(list(Support._value2member_map_));self.reasoning_supported.setCurrentText(self.v2.reasoning.get('reasoning_supported','UNKNOWN'))
        self.efforts=accessible(QLineEdit(', '.join(self.v2.reasoning.get('supported_efforts',[]))),'Deklarierte Reasoning Efforts')
        for title,widget in [('Sprachen (z. B. de, en, fr)',self.languages),('Mehrsprachig',self.multilingual),('Primäre Testsprache',self.primary_language),('Reasoning',self.reasoning_supported),('Efforts (optional)',self.efforts)]:language_form.addRow(title,widget)
        caps_layout.addLayout(language_form);self.profile_tabs.addTab(caps_page,'Fähigkeiten')
        self.card_evidence=QPlainTextEdit('Keine zugeordnete beobachtete Evidenz. Deklarationen erzeugen keine Samples. Grundausbildung startet ausschließlich manuell im Erfahrung-Bereich; dort Lite / Medium / Hard wählen.')
        self.card_evidence.setReadOnly(True)
        evidence_page=QWidget();evidence_layout=QVBoxLayout(evidence_page);evidence_layout.addWidget(self.card_evidence);evidence_layout.addStretch()
        self.profile_tabs.addTab(evidence_page,'Evidenz')
        resource_page=QWidget();resource_form=QFormLayout(resource_page)
        self.max_output=QSpinBox();self.max_output.setRange(0,10000000);self.max_output.setSpecialValueText('Unbekannt');self.max_output.setValue(self.v2.limits.get('max_output_tokens',0))
        self.memory_estimate=QLineEdit(str(self.v2.resources.get('local_memory_estimate',0)))
        self.quantization=QLineEdit(self.v2.identity.get('quantization',''));self.parameter_class=QLineEdit(self.v2.identity.get('parameter_class',''))
        for title,widget in [('Max Output Tokens (Deklaration)',self.max_output),('Lokaler Speicher (Bytes, 0=unbekannt)',self.memory_estimate),('Quantisierung (falls bekannt)',self.quantization),('Parameterklasse (falls bekannt)',self.parameter_class)]:resource_form.addRow(title,widget)
        resource_note=QLabel('Ressourcenklasse und Kontext unter Übersicht. Unbekannte Messwerte bleiben unbekannt; kein globaler Modellscore. Efforts werden gespeichert, aber noch nicht ausgewählt.');resource_note.setWordWrap(True);resource_form.addRow(resource_note)
        self.profile_tabs.addTab(resource_page,'Ressourcen')
        self.populate_observed_card(parent,resource_form)
        layout.addWidget(self.profile_tabs)
        self.status = accessible(QLabel("NONE: Controller sammelt Daten. NATIVE: echte Tool Calls.\nJSON_FALLBACK: streng validierter Ollama-Kompatibilitätsmodus."), "Status des Modellprofils")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.controls = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        self.controls.button(QDialogButtonBox.StandardButton.Save).setText("Profil übernehmen")
        self.controls.button(QDialogButtonBox.StandardButton.Cancel).setText("Abbrechen")
        self.controls.accepted.connect(self.save)
        self.controls.rejected.connect(self.reject)
        layout.addWidget(self.controls)
        self.provider.currentTextChanged.connect(self.provider_changed)
        self.role.currentTextChanged.connect(self.role_changed)

    def populate_observed_card(self,parent,resource_form):
        main=parent.parent() if parent else None
        store=getattr(main,'experience',None)
        if not store:return
        from .onboarding import evidence_view
        from PyQt6.QtWidgets import QPlainTextEdit,QToolButton
        with store.connection() as db:
            row=db.execute('SELECT fingerprint_id FROM model_profile_identity WHERE profile_id=?',(self.profile.id,)).fetchone()
            if not row:return
            fingerprint=row[0]
            runs=[dict(r) for r in db.execute('SELECT scope,status,completed_tasks,planned_tasks FROM benchmark_runs WHERE id IN (SELECT DISTINCT benchmark_id FROM model_experience WHERE model_fingerprint_id=?) ORDER BY started_at DESC LIMIT 1',(fingerprint,))]
        data=evidence_view(store,fingerprint)
        lines=['BEOBACHTET · aktuelle konkrete Modellidentität',str(runs[0]) if runs else 'Keine Grundausbildung zugeordnet.']
        for title,key in [('Aufgaben','capabilities'),('Protokolle','protocols'),('Sprachen','languages'),('Historisch / nicht umetikettiert','legacy')]:
            lines.append(title)
            for name,sources in data[key].items():
                for source,d in sources.items():lines.append(f'{name} · {source.upper()}: {d["samples"]} Samples / {d["verified_successes"]} Erfolg / {d["verified_failures"]} Fehler / {d["unknown"]} unbekannt')
        self.card_evidence.setPlainText('\n'.join(lines))
        import json
        resource_form.addRow(QLabel('Beobachtet (Benchmark / Live getrennt):'))
        observed=QPlainTextEdit(json.dumps(data['resources'],ensure_ascii=False,indent=2));observed.setReadOnly(True);resource_form.addRow(observed)
        toggle=QToolButton();toggle.setText('Fingerprint anzeigen');toggle.setCheckable(True)
        detail=QLabel(fingerprint);detail.setTextInteractionFlags(__import__('PyQt6.QtCore',fromlist=['Qt']).Qt.TextInteractionFlag.TextSelectableByMouse);detail.setVisible(False)
        toggle.toggled.connect(detail.setVisible);resource_form.addRow(toggle);resource_form.addRow(detail)

    def provider_changed(self, provider):
        self.url.setText("http://localhost:1234/v1" if provider == "lmstudio" else "http://localhost:11434/v1")

    def role_changed(self, role):
        defaults = {"general": "chat, general, summarization", "coding": "code_review, coding, debugging", "reasoning": "deep_reasoning, planning"}
        canonical = ", ".join(sorted(c.strip() for c in self.capabilities.text().split(",") if c.strip()))
        if canonical in {", ".join(sorted(x.split(", "))) for x in defaults.values()} or canonical == "chat, general":
            self.capabilities.setText(defaults[role])

    def probe(self):
        if self.worker:
            return
        try:
            backend = create_backend(ModelSettings(self.provider.currentText(), self.url.text().strip(), "", min(5, self.timeout), self.secret_reference.text().strip()))
            self.worker = AgentWorker(backend=backend)
            self.worker.models_ready.connect(self.models_ready)
            self.worker.failure.connect(self.status.setText)
            self.worker.finished.connect(self.probe_finished)
            for widget in (self.probe_button, self.provider, self.url, self.model_name):
                widget.setEnabled(False)
            self.controls.button(QDialogButtonBox.StandardButton.Save).setEnabled(False)
            self.status.setText("Frage lokalen Server …")
            self.worker.start()
        except Exception as exc:
            self.status.setText(str(exc))

    def models_ready(self, result):
        current = self.model_name.currentText()
        self.model_name.clear()
        self.model_name.addItems(result["models"])
        if current in result["models"]:
            self.model_name.setCurrentText(current)
        caps = result["capabilities"]
        native = "ja" if caps["tool_calling"] is True else "nein" if caps["tool_calling"] is False else "unbekannt"
        self.status.setText(f"{len(result['models'])} Modelle gemeldet · Native Tools: {native}.\nRolle und Fähigkeiten werden ausdrücklich von dir konfiguriert.")

    def probe_finished(self):
        worker, self.worker = self.worker, None
        for widget in (self.probe_button, self.provider, self.url, self.model_name):
            widget.setEnabled(True)
        self.controls.button(QDialogButtonBox.StandardButton.Save).setEnabled(True)
        worker.deleteLater()
        if self.close_after_probe:
            super().reject()

    def save(self):
        try:
            profile = ModelProfile(self.identifier.text().strip(), self.display_name.text().strip(),
                self.provider.currentText(), self.model_name.currentText().strip(), self.url.text().strip(),
                self.role.currentText(), [c.strip() for c in self.capabilities.text().split(",") if c.strip()],
                self.tool_mode.currentText(), self.priority.value(), self.enabled.isChecked(),
                self.speed.currentText(), self.resource.currentText(), self.context.value())
            from dataclasses import asdict
            for (dimension,name),combo in self.v2_selectors.items():getattr(self.v2,dimension)[name]=combo.currentText()
            self.v2.migration_version='explicit-v2'
            self.v2.languages={'declared_multilingual':self.multilingual.currentText(),'declared_languages':[x.strip() for x in self.languages.text().split(',') if x.strip()],'primary_language':self.primary_language.text().strip()}
            self.v2.reasoning={'reasoning_supported':self.reasoning_supported.currentText(),'supported_efforts':[x.strip() for x in self.efforts.text().split(',') if x.strip()]}
            self.v2.limits.update(context_tokens=self.context.value(),max_output_tokens=self.max_output.value())
            self.v2.resources.update(resource_class=self.resource.currentText(),local_memory_estimate=int(self.memory_estimate.text()))
            self.v2.identity.update(quantization=self.quantization.text().strip(),parameter_class=self.parameter_class.text().strip())
            self.v2.validate();profile.profile_v2=asdict(self.v2)
            profile.secret_reference=self.secret_reference.text().strip()
            profile.validate()
            if self.secret_value.text():
                from .security import save_secret
                profile.secret_reference=save_secret(profile.provider,profile.base_url,self.secret_value.text())
                self.secret_value.clear()
            self.profile = profile
            self.accept()
        except Exception as exc:
            QMessageBox.warning(self, "Modellprofil prüfen", str(exc))

    def reject(self):
        if self.worker:
            self.close_after_probe = True
            self.worker.cancel()
        else:
            super().reject()

    def closeEvent(self, event):
        if self.worker:
            self.reject()
            event.ignore()
        else:
            event.accept()


class ModelsDialog(QDialog):
    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self.settings = deepcopy(settings)
        self.setWindowTitle("Modelle und automatisches Routing")
        self.resize(1000, 650)
        if not self.settings.models and (settings.model.model or settings.model.provider == "mock"):
            self.settings.models = list(ModelRegistry.from_settings(settings).profiles.values())
        layout = QVBoxLayout(self)
        note = QLabel("Fähigkeiten und Ressourcenklassen sind deine Konfiguration. Es werden keine Modelle heruntergeladen.\nAUTO bevorzugt die kleinste geeignete Ressourcenklasse; ein einzelnes Modell bleibt für alle Aufgaben nutzbar.")
        note.setWordWrap(True)
        layout.addWidget(note)
        self.table = accessible(QTableWidget(0, 7), "Konfigurierte Modellprofile")
        self.table.setHorizontalHeaderLabels(["Aktiv", "Name", "Provider", "Rolle", "Tools", "Geschwindigkeit", "Ressourcen"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.itemDoubleClicked.connect(lambda _item: self.edit())
        self.table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.table)
        row = QHBoxLayout()
        for title, action in [("Modell hinzufügen", self.add), ("Profil bearbeiten", self.edit), ("Profil entfernen", self.remove)]:
            row.addWidget(button(title, action))
        layout.addLayout(row)
        form = QFormLayout()
        self.default = accessible(QComboBox(), "Standardmodell für AUTO")
        self.fallback = accessible(QComboBox(), "General-Fallback-Modell")
        self.escalation = accessible(QCheckBox("Eskalation bei überprüfbarem Fehlschlag erlauben"), "Eskalation erlauben")
        self.escalation.setChecked(settings.routing.allow_escalation)
        self.limit = accessible(QSpinBox(), "Maximale Eskalationen pro Aufgabe")
        self.limit.setRange(0, 3)
        self.limit.setValue(settings.routing.max_escalations_per_task)
        for title, widget in [("Standard (bei Gleichstand)", self.default), ("Fallback", self.fallback), ("", self.escalation), ("Maximale Modellwechsel", self.limit)]:
            form.addRow(title, widget)
        layout.addLayout(form)
        controls = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        controls.button(QDialogButtonBox.StandardButton.Save).setText("Modelle speichern und prüfen")
        controls.button(QDialogButtonBox.StandardButton.Cancel).setText("Abbrechen")
        controls.accepted.connect(self.save)
        controls.rejected.connect(self.reject)
        layout.addWidget(controls)
        self.refresh()

    def refresh(self):
        default = self.default.currentData() or self.settings.routing.default_model
        fallback = self.fallback.currentData() or self.settings.routing.fallback_model
        self.table.setRowCount(len(self.settings.models))
        self.default.clear()
        self.fallback.clear()
        for row, p in enumerate(self.settings.models):
            for col, value in enumerate(["ja" if p.enabled else "nein", p.display_name, p.provider, p.role, p.tool_mode, p.speed_class, p.resource_class]):
                item = QTableWidgetItem(value)
                item.setToolTip(f"{p.id} · {p.model_name}\n{p.base_url}\nFähigkeiten: {', '.join(p.capabilities)}")
                self.table.setItem(row, col, item)
            if p.enabled:
                self.default.addItem(p.display_name, p.id)
                self.fallback.addItem(p.display_name, p.id)
        for combo, value in [(self.default, default), (self.fallback, fallback)]:
            if combo.findData(value) >= 0:
                combo.setCurrentIndex(combo.findData(value))
        self.table.resizeColumnsToContents()

    def add(self):
        identifiers = {p.id for p in self.settings.models}
        index = 1
        while f"model_{index}" in identifiers:
            index += 1
        old = self.settings.model
        profile = ModelProfile(f"model_{index}", "Neues Modell", old.provider, old.model,
                               old.base_url, tool_mode="none")
        dialog = ProfileDialog(profile, old.timeout, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            if dialog.profile.id in identifiers:
                QMessageBox.warning(self, "Modell-ID", "Diese ID ist bereits vergeben.")
                return
            self.settings.models.append(dialog.profile)
            self.refresh()

    def edit(self):
        row = self.table.currentRow()
        if not 0 <= row < len(self.settings.models):
            return
        old = self.settings.models[row]
        dialog = ProfileDialog(old, self.settings.model.timeout, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            if any(p.id == dialog.profile.id for index, p in enumerate(self.settings.models) if index != row):
                QMessageBox.warning(self, "Modell-ID", "Diese ID ist bereits vergeben.")
                return
            self.settings.models[row] = dialog.profile
            for combo, key in [(self.default, "default_model"), (self.fallback, "fallback_model")]:
                if combo.currentData() == old.id:
                    combo.setItemData(combo.currentIndex(), dialog.profile.id)
                    setattr(self.settings.routing, key, dialog.profile.id)
            if self.settings.routing.manual_model == old.id:
                self.settings.routing.manual_model = dialog.profile.id
            self.refresh()

    def remove(self):
        row = self.table.currentRow()
        if 0 <= row < len(self.settings.models):
            self.settings.models.pop(row)
            self.refresh()

    def save(self):
        self.settings.routing.default_model = self.default.currentData() or ""
        self.settings.routing.fallback_model = self.fallback.currentData() or ""
        self.settings.routing.allow_escalation = self.escalation.isChecked()
        self.settings.routing.max_escalations_per_task = self.limit.value()
        if self.settings.routing.manual_model not in {p.id for p in self.settings.models if p.enabled}:
            self.settings.routing.manual_model = ""
            self.settings.routing.mode = "auto"
        try:
            self.settings.validate()
        except Exception as exc:
            QMessageBox.warning(self, "Modelle prüfen", str(exc))
            return
        self.accept()
