"""Experience and explicit benchmark controls, reusing the existing Qt worker."""
from PyQt6.QtWidgets import (QWidget, QDialog, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel,
    QListWidget, QListWidgetItem, QComboBox, QCheckBox, QSpinBox, QDoubleSpinBox,
    QDialogButtonBox, QTableWidget, QTableWidgetItem, QAbstractItemView, QPlainTextEdit, QMessageBox, QToolButton, QHeaderView)
from PyQt6.QtCore import Qt
from .dialogs import accessible, button
from .taxonomy import TaskCategory
from .benchmark import BenchmarkRunner, suite
from .experience import SuccessSignal,confidence
from .training import DOMAINS,training_suite,plan_for
from .routing import ModelRegistry
from .worker import AgentWorker


class BenchmarkDialog(QDialog):
    def __init__(self, registry, parent=None):
        super().__init__(parent)
        self.registry=registry
        self.setWindowTitle('Modell-Grundausbildung · bewusst Compute verwenden')
        self.resize(720, 670)
        layout = QVBoxLayout(self)
        note = QLabel('Jeder Test erzeugt neue Modellaufrufe und kostet Zeit sowie CPU/GPU-Rechenleistung.\nEs werden nur synthetische Fixtures verwendet. Ergebnisse bleiben kompakt; vollständige Antworten werden nicht gespeichert.\nDemo-Profile liefern ausschließlich simulierte Ergebnisse.')
        note.setWordWrap(True)
        layout.addWidget(note)
        self.models = accessible(QListWidget(), 'Benchmark-Modelle')
        for p in registry.enabled():
            item = QListWidgetItem(p.display_name + (' · DEMO' if p.provider == 'mock' else ' · ' + p.model_name))
            item.setData(Qt.ItemDataRole.UserRole, p.id)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Unchecked)
            self.models.addItem(item)
        layout.addWidget(self.models)
        self.scope = accessible(QComboBox(), 'Testumfang')
        self.scope.addItem('Lite · schnell · wenige Tests · geringe Start-Evidenz', 'lite')
        self.scope.addItem('Medium · ausgewogen · brauchbares Startprofil', 'medium')
        self.scope.addItem('Hard · umfangreich · langsam · breitere Start-Evidenz', 'hard')
        self.scope.setCurrentIndex(1)
        layout.addWidget(self.scope)
        self.categories = {}
        grid = QGridLayout()
        for i, category in enumerate(DOMAINS):
            check = accessible(QCheckBox(str(category)), str(category))
            check.setChecked(True)
            self.categories[str(category)] = check
            grid.addWidget(check, i//2, i%2)
            check.toggled.connect(self.update_count)
        layout.addLayout(grid)
        self.count = QLabel()
        self.count.setWordWrap(True)
        layout.addWidget(self.count)
        self.controls = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.controls.button(QDialogButtonBox.StandardButton.Ok).setText('Benchmark starten')
        self.controls.button(QDialogButtonBox.StandardButton.Cancel).setText('Abbrechen')
        self.controls.accepted.connect(self.accept)
        self.controls.rejected.connect(self.reject)
        layout.addWidget(self.controls)
        self.models.itemChanged.connect(self.update_count)
        self.scope.currentIndexChanged.connect(self.update_count)
        self.update_count()

    def selected_models(self):
        return [self.models.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.models.count())
                if self.models.item(i).checkState() == Qt.CheckState.Checked]

    def selected_categories(self):
        return [key for key, check in self.categories.items() if check.isChecked()]

    def update_count(self):
        if not hasattr(self, 'count'):
            return
        models, categories = self.selected_models(), self.selected_categories()
        from .onboarding import catalog_v2
        tasks=[t for t in catalog_v2() if t.category in categories] if categories else []
        counts={key:len(plan_for(self.registry.profiles[key],tasks,self.scope.currentData())) for key in models}
        count=sum(counts.values())
        self.count.setText(f'{count} primäre Aufgaben; höchstens {2*count} Modellaufrufe einschließlich begrenzter Resource Recovery.\n'+
            ', '.join(self.registry.profiles[key].display_name+': '+str(n) for key,n in counts.items())+
            '\nHARD liefert mehr Evidenz, keine Qualitätsgarantie. Capability Cards reduzieren Aufgaben. NONE hat keine nativen Tooltests. Kleine Samples bleiben VERY_LOW/LOW; keine Qualitätsgarantie. STOP beendet den Lauf als ABORTED.')
        self.controls.button(QDialogButtonBox.StandardButton.Ok).setEnabled(bool(models and categories and count))


class ExperiencePanel(QWidget):
    def __init__(self, main):
        super().__init__(main)
        self.main = main
        self.rows = []
        layout = QVBoxLayout(self)
        note = QLabel('ANGEGEBEN: konfiguriert in Modelle verwalten. GEMESSEN: lokale Versuche für eine konkrete Modellidentität.\nUNKNOWN zählt nicht als Erfolg. Wenige Versuche sind keine verlässliche Qualitätsaussage; Token-Schätzungen sind mit ≈ markiert.')
        note.setWordWrap(True)
        layout.addWidget(note)
        row = QHBoxLayout()
        self.advanced_toggle = accessible(QToolButton(), 'Training und Routing-Einstellungen anzeigen')
        self.advanced_toggle.setObjectName('disclosure')
        self.advanced_toggle.setText('Erweitert: Training und Routing')
        self.advanced_toggle.setCheckable(True)
        self.advanced_toggle.setArrowType(Qt.ArrowType.RightArrow)
        self.advanced_toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        row.addWidget(self.advanced_toggle)
        row.addStretch()
        self.benchmark_button = button('Modell-Grundausbildung', self.start_benchmark)
        self.refresh_button = button('Erfahrung aktualisieren', self.refresh)
        self.stale_button = button('Auswahl als STALE markieren', self.mark_stale)
        self.stop_button = button('STOP · Abbrechen', main.stop)
        self.stop_button.setEnabled(False)
        row.addWidget(self.refresh_button)
        row.addWidget(self.stop_button)
        layout.addLayout(row)
        advanced = QWidget()
        advanced_layout = QVBoxLayout(advanced)
        advanced_layout.setContentsMargins(0, 0, 0, 0)
        actions = QHBoxLayout()
        actions.addWidget(self.benchmark_button)
        actions.addWidget(self.stale_button)
        actions.addStretch()
        advanced_layout.addLayout(actions)
        self.progress=QLabel('Grundausbildung nur manuell. Live-Nutzung ergänzt später die Erfahrung.')
        self.progress.setWordWrap(True)
        advanced_layout.addWidget(self.progress)
        controls = QHBoxLayout()
        self.use = accessible(QCheckBox('AUTO nutzt Erfahrung'), 'Erfahrungsrouting verwenden')
        self.use.setChecked(main.settings.routing.use_experience)
        self.minimum = accessible(QSpinBox(), 'Mindestanzahl geprüfter Versuche')
        self.minimum.setRange(1, 1000)
        self.minimum.setValue(main.settings.routing.min_samples_for_experience)
        self.quality = accessible(QDoubleSpinBox(), 'Mindestqualität in Prozent')
        self.quality.setRange(0, 100)
        self.quality.setValue(main.settings.routing.quality.get('default_min_success_rate', .8)*100)
        self.apply_button = button('Routing speichern', self.save_routing)
        for w in (self.use, QLabel('Min. Versuche:'), self.minimum, QLabel('Qualität %:'), self.quality, self.apply_button):
            controls.addWidget(w)
        advanced_layout.addLayout(controls)
        sources = QHBoxLayout()
        sources.addWidget(QLabel('Experience Sources:'))
        self.local_source = accessible(QCheckBox('Lokale Erfahrung verwenden'), 'Lokale Erfahrung verwenden')
        self.community_source = accessible(QCheckBox('Community-Erfahrung verwenden'), 'Community-Erfahrung verwenden')
        self.local_source.setChecked(main.settings.experience_sources.local_live_enabled)
        self.community_source.setChecked(main.settings.experience_sources.community_enabled)
        sources.addWidget(self.local_source); sources.addWidget(self.community_source)
        sources.addWidget(QLabel('Community: Not configured / No verified cards'))
        sources.addStretch()
        advanced_layout.addLayout(sources)
        advanced.setVisible(False)
        self.advanced_toggle.toggled.connect(advanced.setVisible)
        self.advanced_toggle.toggled.connect(lambda checked: self.advanced_toggle.setArrowType(Qt.ArrowType.DownArrow if checked else Qt.ArrowType.RightArrow))
        layout.addWidget(advanced)
        self.table = accessible(QTableWidget(0, 9), 'Gemessene Modellerfahrung')
        self.table.setHorizontalHeaderLabels(['Modell', 'Aufgabe', 'Versuche / geprüft', 'Erfolg', 'Median Zeit', 'Tokens ein / aus', 'Tool-Fehler', 'Status', 'Letzter Versuch'])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.itemSelectionChanged.connect(self.show_details)
        layout.addWidget(self.table, 2)
        self.details_toggle = accessible(QToolButton(), 'Modellidentität und Messdetails anzeigen')
        self.details_toggle.setObjectName('disclosure')
        self.details_toggle.setText('Modellidentität und Messdetails')
        self.details_toggle.setCheckable(True)
        self.details_toggle.setArrowType(Qt.ArrowType.RightArrow)
        self.details_toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        layout.addWidget(self.details_toggle)
        self.details = accessible(QPlainTextEdit(), 'Angegebene Fähigkeiten und gemessene Erfahrung')
        self.details.setObjectName('technical')
        self.details.setReadOnly(True)
        self.details.setVisible(False)
        self.details_toggle.toggled.connect(self.details.setVisible)
        self.details_toggle.toggled.connect(lambda checked: self.details_toggle.setArrowType(Qt.ArrowType.DownArrow if checked else Qt.ArrowType.RightArrow))
        layout.addWidget(self.details, 1)
        self.refresh()

    def set_busy(self, busy):
        self.stop_button.setEnabled(busy)
        for w in (self.benchmark_button, self.refresh_button, self.apply_button, self.use, self.minimum, self.quality):
            w.setEnabled(not busy)
        self.stale_button.setEnabled(not busy and self.table.currentRow() >= 0)

    def save_routing(self):
        self.main.settings.experience_sources.local_live_enabled = self.local_source.isChecked()
        self.main.settings.experience_sources.community_enabled = self.community_source.isChecked()
        s = self.main.settings.routing
        s.use_experience = self.use.isChecked()
        s.min_samples_for_experience = self.minimum.value()
        s.quality['default_min_success_rate'] = self.quality.value()/100
        self.main.save_settings()
        self.refresh()

    def refresh(self):
        selected = self.rows[self.table.currentRow()]['model_fingerprint'] if 0 <= self.table.currentRow() < len(self.rows) else None
        self.table.blockSignals(True)
        self.rows = self.main.experience.summaries()
        self.table.setRowCount(len(self.rows))
        minimum = self.main.settings.routing.min_samples_for_experience
        for row, r in enumerate(self.rows):
            ratio = '—' if r['success_rate'] is None else f"{r['success_rate']:.0%} ({r['hard_sample_count']} geprüft)"
            latency = '—' if r['median_latency'] is None else f"{r['median_latency']/1000:.2f} s"
            tokens = ('≈ ' if r['tokens_estimated'] else '') + f"{r['median_input_tokens'] or 0:g} / {r['median_output_tokens'] or 0:g}"
            state = 'STALE' if r['stale'] else ('LOW_CONFIDENCE · ' if r['hard_sample_count'] < minimum else '')+confidence(r['hard_sample_count'])
            values = [r['display_name'], r['task_category'], f"{r['sample_count']} / {r['hard_sample_count']}", ratio, latency, tokens,
                      '—' if r['invalid_tool_rate'] is None else f"{r['invalid_tool_rate']:.0%}", state, r['last_updated'][:19]]
            for col, value in enumerate(values):
                self.table.setItem(row, col, QTableWidgetItem(value))
        self.table.clearSelection()
        self.table.setCurrentCell(-1, -1)
        if selected:
            for i, r in enumerate(self.rows):
                if r['model_fingerprint'] == selected:
                    self.table.selectRow(i)
                    break
        self.table.blockSignals(False)
        self.show_details()

    def show_details(self):
        import json
        row = self.table.currentRow()
        self.stale_button.setEnabled(self.main.worker is None and 0 <= row < len(self.rows))
        if not 0 <= row < len(self.rows):
            declared = '\n'.join(p.display_name + ': ' + ', '.join(p.capabilities) for p in self.main.settings.models)
            self.details.setPlainText('ANGEGEBENE FÄHIGKEITEN\n' + (declared or 'Bisheriges Einzelmodell') + '\n\nGEMESSENE ERFAHRUNG\nNoch keine Auswahl. Für Modelle ohne aktuelle Daten gibt es noch keine Erfahrungsdaten. Grundausbildung nur manuell starten. Benchmark = Grundausbildung; Live-Nutzung = Weiterbildung.')
            return
        r = self.rows[row]
        identity = json.loads(r['identity_json'])
        matching = [p for p in self.main.settings.models if p.model_name == identity['model'] and p.base_url == identity['endpoint']]
        caps = '\n'.join(p.display_name + ': ' + ', '.join(p.capabilities) for p in matching) or 'Profil nicht mehr konfiguriert'
        self.details.setPlainText(f"ANGEGEBENE FÄHIGKEITEN (Konfiguration)\n{caps}\n\nGEMESSENE ERFAHRUNG\n{r['task_category']}: {r['verified_success_count']} verifizierte Erfolge, {r['verified_failure_count']} verifizierte Fehler; Benutzerfeedback +{r['user_success_count']}/−{r['user_failure_count']}.\n{r['sample_count']} Versuche, {r['hard_sample_count']} harte Bewertungen. Unbewertete Antworten bleiben UNKNOWN.\nStatus: {'STALE: '+str(r['stale_reason']) if r['stale'] else 'aktuelle Identität'}\n\nFINGERPRINT\n{r['model_fingerprint']}\n{json.dumps(identity, ensure_ascii=False, indent=2)}")
        from .onboarding import evidence_view
        evidence=evidence_view(self.main.experience,r['model_fingerprint'])
        self.details.appendPlainText('\nV2 EVIDENZ (Benchmark und Live getrennt; fehlende Sprachen bleiben unbekannt)\n'+json.dumps(evidence,ensure_ascii=False,indent=2))
        breakdown=self.main.experience.training_breakdown(r['model_fingerprint'])
        lines=['\nDOMAINS / fachliche Evidenz']
        for title,key in [('Domains','domains'),('Response Contracts / Protokoll','contracts')]:
            lines.append(title)
            for name,data in breakdown[key].items():
                lines.append(f"{name}: Samples {data['sample_count']}, geprüft {data['hard_sample_count']}, Success {data['success_count']}, Confidence {data['confidence']}, Formatfehler {data['format_failures']}, Inhaltsfehler {data['content_failures']} · Quellen "+', '.join(s.upper() for s in data['sources']))
        lines.append('Kleine Stichproben sind keine statistische Garantie. FORMAT_FAILURE ist kein allgemeines Domain-Versagen.')
        self.details.appendPlainText('\n'.join(lines))

    def mark_stale(self):
        row = self.table.currentRow()
        if self.main.worker is None and 0 <= row < len(self.rows):
            self.main.experience.mark_stale(self.rows[row]['model_fingerprint'], 'USER_MARKED_STALE')
            self.refresh()

    def start_benchmark(self):
        if self.main.worker:
            return
        registry = self.main.registry or ModelRegistry.from_settings(self.main.settings)
        dialog = BenchmarkDialog(registry, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        runner = BenchmarkRunner(registry, self.main.settings, self.main.experience, dialog.selected_models(),
                                 dialog.selected_categories(), dialog.scope.currentData(), session_id=self.main.event_log.session_id)
        self.main.last_experience_id = None
        self.main.status.setText('Benchmark wird gestartet …')
        self.main.bind_worker(AgentWorker(agent=runner, task='', log=self.main.event_log, permissions=self.main.permissions))
