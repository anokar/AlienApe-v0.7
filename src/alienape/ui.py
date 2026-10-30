from datetime import datetime
from pathlib import Path
import hashlib
import html
import json
import os
import threading
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QFont, QKeySequence, QShortcut, QPixmap
from PyQt6.QtWidgets import (QAbstractItemView, QApplication, QCheckBox, QComboBox,
    QDialog, QFileDialog, QHBoxLayout, QHeaderView, QLabel, QListWidget, QMainWindow,
    QMessageBox, QPlainTextEdit, QSplitter, QTabWidget, QTableWidget, QTableWidgetItem,
    QTextBrowser, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget, QSizePolicy, QGridLayout, QLineEdit, QFrame, QToolButton, QScrollArea)
from .agent import Agent, Session
from . import __version__
from .config import bundled_directory
from .backends import create_backend
from .dialogs import accessible, button, SettingsDialog, PermissionDialog, MemoryDialog
from .events import EventLog, LABELS
from .memory import MemoryStore
from .permissions import PermissionManager
from .tools import Workspace
from .worker import AgentWorker
from .routing import ModelRegistry, RegistryProbe
from .routed_agent import RoutedAgent
from .model_dialogs import ModelsDialog
from .experience import ExperienceStore
from .experience_ui import ExperiencePanel
from .ui_theme import DisplayLabel, palette, stylesheet
from .diagnostics import DiagnosticStore
from .i18n import localize


class MainWindow(QMainWindow):
    def __init__(self, settings, directory):
        super().__init__()
        self.settings, self.directory = settings, Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.worker = self.permission_dialog = None
        self.registry = None
        self.closing = False
        self.workspace = self.memory = None
        self.session = Session()
        self.permissions = PermissionManager(settings.permissions)
        self.event_log = EventLog(self.directory / "logs")
        self.diagnostics=DiagnosticStore(self.directory/'diagnostics')
        self.experience = ExperienceStore(self.directory / 'model_experience.sqlite3')
        self.last_experience_id = None
        self.setWindowTitle(f"AlienApe · Desktop Agent v{__version__}")
        self.resize(1320, 920)
        self.setMinimumSize(820, 520)
        central = QWidget()
        self.setCentralWidget(central)
        outer = QVBoxLayout(central)
        outer.setContentsMargins(12, 8, 12, 6)
        outer.setSpacing(8)
        header = QHBoxLayout()
        brand_image = QPixmap(str(bundled_directory() / 'assets' / 'splash.png'))
        self.brand_icon = accessible(QLabel(), "AlienApe Logo")
        self.brand_icon.setFixedSize(32, 32)
        if not brand_image.isNull():
            ratio = self.devicePixelRatioF()
            icon = brand_image.scaled(round(32 * ratio), round(32 * ratio), Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
            icon.setDevicePixelRatio(ratio)
            self.brand_icon.setPixmap(icon)
            header.addWidget(self.brand_icon)
        title = QLabel("AlienApe")
        title.setObjectName("brand")
        header.addWidget(title)
        subtitle = QLabel("LOCAL AI WORKSPACE")
        subtitle.setObjectName("eyebrow")
        header.addSpacing(8)
        header.addWidget(subtitle)
        header.addStretch()
        header.addWidget(QLabel(localize("UI_MODEL",settings.ui.language)))
        self.model = accessible(QComboBox(), "Aktives Modell")
        self.model.setMinimumWidth(140)
        self.model.setMaximumWidth(290)
        self.model.currentTextChanged.connect(self.model_changed)
        header.addWidget(self.model, 1)
        self.models_button = button(localize("MODELS",settings.ui.language), self.open_models, "Modelle verwalten")
        self.settings_button = button(localize("SETTINGS",settings.ui.language), self.open_settings)
        header.addWidget(self.models_button)
        header.addWidget(self.settings_button)
        outer.addLayout(header)
        separator = QFrame()
        separator.setObjectName("separator")
        separator.setFixedHeight(1)
        outer.addWidget(separator)
        project_row = QHBoxLayout()
        project_caption = QLabel(localize("UI_PROJECT",settings.ui.language))
        project_caption.setObjectName("eyebrow")
        project_row.addWidget(project_caption)
        self.project_path = accessible(QLineEdit("Kein Projekt geöffnet"), "Aktueller Projektordner")
        self.project_path.setReadOnly(True)
        self.project_path.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        project_row.addWidget(self.project_path, 1)
        self.project_button = button(localize("PROJECT_OPEN",settings.ui.language), self.choose_project, "Projektordner wählen · Strg+O")
        project_row.addWidget(self.project_button)
        outer.addLayout(project_row)
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setChildrenCollapsible(False)
        outer.addWidget(splitter, 1)
        project = QFrame()
        project.setObjectName("surface")
        project.setMinimumWidth(190)
        project.setMaximumWidth(340)
        left = QVBoxLayout(project)
        left.setContentsMargins(8, 6, 8, 6)
        left.setSpacing(6)
        files_caption = QLabel(localize("UI_FILES",settings.ui.language))
        files_caption.setObjectName("eyebrow")
        left.addWidget(files_caption)
        self.files = accessible(QTreeWidget(), "Projektdateien", "Doppelklick übernimmt einen Lesebefehl in das Eingabefeld.")
        self.files.setObjectName("explorer")
        self.files.setHeaderLabels(["Dateien"])
        self.files.setHeaderHidden(True)
        self.files.itemExpanded.connect(self.expand_directory)
        self.files.itemDoubleClicked.connect(self.file_selected)
        left.addWidget(self.files, 1)
        refresh = button(localize("UI_REFRESH",settings.ui.language), self.refresh_files)
        refresh.setObjectName("quickAction")
        left.addWidget(refresh)
        recent_caption = QLabel(localize("UI_RECENT",settings.ui.language))
        recent_caption.setObjectName("eyebrow")
        left.addWidget(recent_caption)
        self.recent = accessible(QListWidget(), "Zuletzt verwendete Dateien")
        self.recent.setObjectName("recent")
        self.recent.setMaximumHeight(90)
        self.recent.itemDoubleClicked.connect(lambda item: self.set_prompt(f'Lies "{item.text()}"'))
        left.addWidget(self.recent)
        self.details_toggle = accessible(QToolButton(), "Verbindung und Ressourcen anzeigen")
        self.details_toggle.setObjectName("disclosure")
        self.details_toggle.setText("Verbindung und Ressourcen")
        self.details_toggle.setCheckable(True)
        self.details_toggle.setArrowType(Qt.ArrowType.RightArrow)
        self.details_toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        left.addWidget(self.details_toggle)
        self.details_area = QScrollArea()
        self.details_area.setWidgetResizable(True)
        self.details_area.setMaximumHeight(270)
        detail_body = QWidget()
        detail_layout = QVBoxLayout(detail_body)
        detail_layout.setContentsMargins(2, 6, 4, 6)
        self.connection = accessible(DisplayLabel("Demo wird vorbereitet …"), "Verbindungsstatus")
        self.active_model = accessible(DisplayLabel("Arbeitendes Modell: noch keine Aufgabe"), "Arbeitendes Modell und Routing-Grund")
        self.resources = accessible(DisplayLabel("Ressourcen: noch keine Aufgabe ausgeführt"), "Ressourcenverbrauch")
        self.per_model_text = accessible(DisplayLabel(), "Ressourcen pro Modell")
        self.semantic_status=accessible(DisplayLabel("Semantic Intent Resolver: aus"),"Semantic Resolver Status")
        self.semantic_status.setVisible(self.settings.semantic_resolver.enabled)
        for label in [self.connection, self.semantic_status, self.active_model, self.resources, self.per_model_text]:
            label.setObjectName("muted")
            detail_layout.addWidget(label)
        self.per_model_text.setVisible(False)
        self.test_button = button(localize("UI_CONNECT",settings.ui.language), self.refresh_models)
        detail_layout.addWidget(self.test_button)
        self.details_area.setWidget(detail_body)
        self.details_area.setVisible(False)
        self.details_toggle.toggled.connect(self.details_area.setVisible)
        self.details_toggle.toggled.connect(lambda checked: self.details_toggle.setArrowType(Qt.ArrowType.DownArrow if checked else Qt.ArrowType.RightArrow))
        left.addWidget(self.details_area)
        splitter.addWidget(project)
        self.tabs = accessible(QTabWidget(), "Arbeitsbereiche")
        splitter.addWidget(self.tabs)
        splitter.setSizes([245, 1010])
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(8, 8, 0, 0)
        layout.setSpacing(6)
        row = QHBoxLayout()
        hint = QLabel(localize("UI_TASK",settings.ui.language))
        hint.setObjectName("muted")
        row.addWidget(hint)
        row.addStretch()
        self.clear_button = button(localize("UI_CLEAR",settings.ui.language), self.clear_session, "Chat und aktuelle Freigaben leeren. SQLite-Memory bleibt erhalten.")
        self.clear_button.setObjectName("quickAction")
        row.addWidget(self.clear_button)
        layout.addLayout(row)
        self.chat = accessible(QTextBrowser(), "Chatverlauf")
        self.chat.setObjectName("conversation")
        self.chat.setOpenExternalLinks(False)
        self.chat.setOpenLinks(False)
        layout.addWidget(self.chat, 1)
        self.quick_toggle = accessible(QToolButton(), "Vorlagen und Feedback anzeigen")
        self.quick_toggle.setObjectName("disclosure")
        self.quick_toggle.setText("Vorlagen && Feedback")
        self.quick_toggle.setCheckable(True)
        self.quick_toggle.setArrowType(Qt.ArrowType.RightArrow)
        self.quick_toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.quick_toggle.toggled.connect(lambda checked: self.quick_toggle.setArrowType(Qt.ArrowType.DownArrow if checked else Qt.ArrowType.RightArrow))
        layout.addWidget(self.quick_toggle)
        self.quick_actions = QWidget()
        quick_layout = QVBoxLayout(self.quick_actions)
        quick_layout.setContentsMargins(0, 0, 0, 0)
        self.quick_actions.setVisible(False)
        self.quick_toggle.toggled.connect(self.quick_actions.setVisible)
        examples = QHBoxLayout()
        for text, prompt in [("Dateien auflisten", "Liste die Dateien auf"), ("Memory ausprobieren", "Merke dir: Wir verwenden Godot 4.")]:
            example = button(text, lambda _checked=False, value=prompt: self.set_prompt(value))
            example.setObjectName("quickAction")
            examples.addWidget(example)
        examples.addStretch()
        quick_layout.addLayout(examples)
        feedback = QHBoxLayout()
        feedback_note = QLabel(localize("UI_RATE",settings.ui.language))
        feedback_note.setObjectName("muted")
        feedback.addWidget(feedback_note)
        self.feedback_buttons = [button('Funktioniert', lambda: self.rate_answer(True)),
                                 button('Funktioniert nicht', lambda: self.rate_answer(False))]
        for b in self.feedback_buttons:
            b.setEnabled(False)
            b.setObjectName("quickAction")
            feedback.addWidget(b)
        feedback.addStretch()
        quick_layout.addLayout(feedback)
        layout.addWidget(self.quick_actions)
        self.input = accessible(QPlainTextEdit(), "Nachricht an AlienApe", "Strg+Enter sendet. Enter fügt eine neue Zeile ein.")
        self.input.setPlaceholderText(localize('UI_INPUT_HINT',settings.ui.language))
        self.input.setMinimumHeight(64)
        self.input.setMaximumHeight(110)
        layout.addWidget(self.input)
        controls = QHBoxLayout()
        self.send_button = button(localize("UI_SEND",settings.ui.language), self.send, "Nachricht senden · Strg+Enter")
        self.send_button.setObjectName("primary")
        self.stop_button = button(localize("UI_STOP",settings.ui.language), self.stop, "Laufende Anfrage und weitere Werkzeuge abbrechen · Escape")
        self.stop_button.setObjectName("stop")
        self.stop_button.setEnabled(False)
        keyboard_hint = QLabel("Strg+Enter senden · Esc abbrechen")
        keyboard_hint.setObjectName("muted")
        controls.addWidget(keyboard_hint)
        controls.addStretch()
        controls.addWidget(self.stop_button)
        controls.addWidget(self.send_button)
        layout.addLayout(controls)
        self.tabs.addTab(page, "Chat")
        self.build_memory_tab()
        self.build_activity_tab()
        self.experience_panel = ExperiencePanel(self)
        self.tabs.addTab(self.experience_panel, 'Modellerfahrung')
        footer = QFrame()
        footer.setObjectName("statusbar")
        footer_layout = QHBoxLayout(footer)
        footer_layout.setContentsMargins(4, 5, 4, 0)
        self.status = accessible(DisplayLabel("Bereit"), "Agent-Status")
        self.status.setMaximumHeight(64)
        footer_layout.addWidget(self.status, 3)
        self.verification_label=accessible(DisplayLabel('Verification: NOT_RUN'),'Objektive Verifikation')
        self.verification_label.setMaximumHeight(64)
        footer_layout.addWidget(self.verification_label, 2)
        outer.addWidget(footer)
        QShortcut(QKeySequence("Ctrl+Return"), self, activated=self.send)
        QShortcut(QKeySequence("Ctrl+O"), self, activated=self.choose_project)
        QShortcut(QKeySequence("Escape"), self, activated=self.stop)
        for before, after in [(self.model, self.project_button), (self.project_button, self.files), (self.files, self.tabs), (self.tabs, self.chat), (self.chat, self.input), (self.input, self.send_button), (self.send_button, self.stop_button)]:
            self.setTabOrder(before, after)
        self.apply_style()
        last = self.directory / "last_project.txt"
        demo = bundled_directory() / "demo_workspace"
        try:
            self.set_workspace(Path(last.read_text(encoding="utf-8")) if last.exists() else demo)
        except Exception:
            if demo.exists():
                self.set_workspace(demo)
        QTimer.singleShot(0, self.refresh_models)

    def build_memory_tab(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        note = QLabel("Explizite Projektinformationen · bearbeitbar · über Sessions hinweg gespeichert")
        note.setWordWrap(True)
        layout.addWidget(note)
        self.memory_table = accessible(QTableWidget(0, 7), "Projekt-Memory")
        self.memory_table.setHorizontalHeaderLabels(["Typ", "Schlüssel", "Inhalt", "Quelle", "Letzter Zugriff", "Zugriffe", "Status"])
        self.memory_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.memory_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.memory_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.memory_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.memory_table.horizontalHeader().setStretchLastSection(True)
        self.memory_table.setColumnWidth(2, 260)
        layout.addWidget(self.memory_table)
        row = QGridLayout()
        self.memory_buttons = []
        for index, (text, action) in enumerate([("Neu", lambda: self.edit_memory(False)), ("Ansehen / Bearbeiten", lambda: self.edit_memory(True)), ("Aktiv / Inaktiv", self.toggle_memory), ("Wichtig umschalten", self.important_memory), ("Löschen", self.delete_memory)]):
            widget = button(text, action)
            row.addWidget(widget, index // 3, index % 3)
            self.memory_buttons.append(widget)
        self.memory_table.itemSelectionChanged.connect(self.update_memory_actions)
        layout.addLayout(row)
        self.tabs.addTab(page, "Memory")

    def build_activity_tab(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        note = QLabel("Beobachtbare Aktionen und Ergebnisse · keine versteckten Modellgedanken")
        note.setWordWrap(True)
        layout.addWidget(note)
        self.activity_list = accessible(QListWidget(), "Aktivitätsverlauf")
        self.activity_list.currentRowChanged.connect(self.show_event_details)
        layout.addWidget(self.activity_list, 2)
        self.technical = accessible(QCheckBox("Technische Details anzeigen"), "Technische Details anzeigen")
        layout.addWidget(self.technical)
        self.event_details = accessible(QPlainTextEdit(), "Technische Ereignisdetails")
        self.event_details.setObjectName("technical")
        self.event_details.setReadOnly(True)
        self.event_details.setVisible(False)
        self.technical.toggled.connect(self.event_details.setVisible)
        layout.addWidget(self.event_details, 1)
        debug_actions=QWidget();debug_row=QHBoxLayout(debug_actions);debug_row.setContentsMargins(0,0,0,0)
        self.export_last_diagnostic=button('Letzte Diagnose exportieren',lambda:self.export_diagnostics(True),'Nur die zuletzt aufgezeichnete Aufgabe; keine rückwirkende Aufzeichnung.')
        self.export_session_diagnostic=button('Session-Diagnosen exportieren',lambda:self.export_diagnostics(False))
        debug_row.addWidget(self.export_last_diagnostic);debug_row.addWidget(self.export_session_diagnostic);debug_row.addStretch()
        debug_actions.setVisible(False);self.technical.toggled.connect(debug_actions.setVisible);layout.addWidget(debug_actions)
        self.events = []
        self.tabs.addTab(page, "Aktivität")

    def apply_style(self):
        font = QFont("Segoe UI", round(10 * self.settings.ui.font_scale))
        QApplication.instance().setFont(font)
        self.setFont(font)
        self.ui_colors = palette(self.settings.ui.high_contrast)
        padding = {"kompakt": 3, "normal": 5, "groß": 8}[self.settings.ui.density]
        self.setStyleSheet(stylesheet(self.ui_colors, padding))

    def append_chat(self, speaker, text, timing=None):
        from .security import redactor
        escaped = html.escape(redactor.text(text)).replace("\n", "<br>")
        c = self.ui_colors
        role_color = c['danger'] if speaker == "Fehler" else c['muted']
        body = (f'<p style="color:{role_color};font-weight:600;margin-top:12px;margin-bottom:4px;">{html.escape(speaker)}</p>'
                f'<p style="line-height:130%;white-space:pre-wrap;margin-top:0;margin-bottom:12px;">{escaped}</p>')
        if speaker == "Fehler":
            body = f'<table width="100%" cellpadding="8" cellspacing="0" bgcolor="{c["error"]}"><tr><td>{body}</td></tr></table>'
        if timing is not None:
            seconds=timing['total_seconds']
            rendered=f'{seconds:.2f} s' if seconds<1 else f'{seconds:.1f} s'
            detail=html.escape('; '.join(str(k)+': '+str(v) for k,v in timing.items()),quote=True)
            body+=f'<p title="{detail}" style="color:{c["muted"]};font-size:9pt;margin-top:0;">{rendered}</p>'
        self.chat.append(body)
        self.chat.verticalScrollBar().setValue(self.chat.verticalScrollBar().maximum())

    def set_prompt(self, text):
        if self.worker or self.input.isReadOnly():
            return
        self.tabs.setCurrentIndex(0)
        self.input.setPlainText(text)
        self.input.setFocus()

    def choose_project(self):
        if self.worker:
            return
        selected = QFileDialog.getExistingDirectory(self, "Projektordner auswählen", str(self.workspace.root) if self.workspace else "")
        if selected:
            try:
                self.set_workspace(selected)
            except Exception as exc:
                QMessageBox.warning(self, "Projektordner", str(exc))

    def set_workspace(self, path):
        workspace = Workspace(path)
        project_id = hashlib.sha256(os.path.normcase(str(workspace.root)).encode()).hexdigest()[:24]
        memory = MemoryStore(self.directory / "memory.sqlite3", project_id)
        self.memory_table.clearSelection()
        self.memory_table.setCurrentCell(-1, -1)
        self.workspace, self.memory = workspace, memory
        self.last_experience_id = None
        for b in self.feedback_buttons:
            b.setEnabled(False)
        self.project_path.setText(str(workspace.root))
        self.project_path.setCursorPosition(0)
        self.project_path.setAccessibleDescription(str(workspace.root))
        self.project_path.setToolTip(str(workspace.root))
        self.event_log = EventLog(self.directory / "logs", project_id)
        self.session.clear()
        self.permissions = PermissionManager(self.settings.permissions)
        self.recent.clear()
        self.chat.clear()
        self.append_chat("AlienApe", f"Projekt bereit: {workspace.root}\nAufgabe eingeben oder eine Projektdatei auswählen.")
        self.refresh_files()
        self.refresh_memory()
        try:
            (self.directory / "last_project.txt").write_text(str(workspace.root), encoding="utf-8")
        except OSError:
            pass

    def populate_directory(self, parent, relative):
        try:
            path = self.workspace.path(relative)
            with os.scandir(path) as scan:
                entries = []
                for index, entry in enumerate(scan):
                    if index >= 500:
                        break
                    try:
                        rel = str(Path(entry.path).relative_to(self.workspace.root))
                        safe = self.workspace.path(rel)
                        entries.append((safe.is_dir(), entry.name, rel))
                    except OSError:
                        continue
            for directory, name, rel in sorted(entries, key=lambda row: (not row[0], row[1].casefold())):
                item = QTreeWidgetItem([name + (" /" if directory else "")])
                item.setData(0, Qt.ItemDataRole.UserRole, rel)
                item.setToolTip(0, rel)
                if directory:
                    item.setChildIndicatorPolicy(QTreeWidgetItem.ChildIndicatorPolicy.ShowIndicator)
                parent.addChild(item) if isinstance(parent, QTreeWidgetItem) else parent.addTopLevelItem(item)
        except OSError as exc:
            self.status.setText(f"Ordner konnte nicht gelesen werden: {exc}")

    def refresh_files(self):
        self.files.clear()
        if self.workspace:
            self.populate_directory(self.files, ".")

    def expand_directory(self, item):
        if item.childCount() == 0:
            self.populate_directory(item, item.data(0, Qt.ItemDataRole.UserRole))

    def file_selected(self, item, _column):
        relative = item.data(0, Qt.ItemDataRole.UserRole)
        try:
            if self.workspace.path(relative).is_file():
                self.set_prompt(f'Lies "{relative}"')
        except OSError:
            pass

    def set_busy(self, busy):
        for widget in [self.send_button, self.project_button, self.settings_button, self.models_button, self.test_button, self.model, self.clear_button, *self.memory_buttons]:
            widget.setEnabled(not busy)
        self.stop_button.setEnabled(busy)
        self.input.setReadOnly(busy)
        self.update_memory_actions()
        self.experience_panel.set_busy(busy)
        for b in self.feedback_buttons:
            b.setEnabled(not busy and self.last_experience_id is not None)

    def bind_worker(self, worker):
        import time
        worker.accepted_at=getattr(self,'_accepted_task_at',None) or time.monotonic()
        self._accepted_task_at=None
        self.worker = worker
        worker.activity.connect(self.on_event)
        worker.failure.connect(self.on_failure)
        worker.answer.connect(lambda text: self.append_chat("AlienApe", text, self.response_timing(worker)))
        worker.permission_needed.connect(self.on_permission)
        worker.models_ready.connect(self.on_models)
        worker.finished.connect(self.worker_finished)
        self.set_busy(True)
        worker.start()

    def send(self):
        if self.worker or not self.input.toPlainText().strip():
            return
        if not self.workspace:
            self.choose_project()
            if not self.workspace:
                return
        import time
        self._accepted_task_at=time.monotonic()
        task = self.input.toPlainText().strip()
        if __import__('re').match(r'(?i)^\s*(?:merk(?:e)?\s+dir|remember)\b',task):
            from .security import redactor
            redactor.sensitive_memory(task)
        try:
            if self.settings.models or self.settings.model.provider != 'mock':
                agent = RoutedAgent(self.workspace, self.settings, self.session, self.memory,
                                    registry=self.registry, manual_model=self.model.currentData() or "", stopped=threading.Event(),
                                    experience=self.experience, session_id=self.event_log.session_id)
                self.active_model.setText("AUTO: Klassifiziere Aufgabe …" if not self.model.currentData() else "Festes Modell: " + self.model.currentText())
            else:
                self.settings.model.model = self.model.currentText()
                backend = create_backend(self.settings.model)
                agent = Agent(backend, self.workspace, self.settings, self.session, self.memory, stopped=threading.Event())
                self.active_model.setText("Arbeitendes Modell: " + self.model.currentText())
            if self.settings.diagnostic_trace:agent.diagnostic_store=self.diagnostics
            self.append_chat("Du", task)
            self.last_experience_id = None
            self.input.clear()
            self.status.setText("Analysiere Aufgabe …")
            self.bind_worker(AgentWorker(agent=agent, task=task, log=self.event_log, permissions=self.permissions))
        except Exception as exc:
            self.on_failure(str(exc))

    def stop(self):
        if self.worker:
            self.status.setText("Stoppe laufende Arbeit …")
            self.worker.cancel()
        if self.permission_dialog:
            self.permission_dialog.reject()

    def export_diagnostics(self,last_only=False):
        try:records=self.diagnostics.records(self.event_log.session_id,last_only)
        except Exception as exc:
            from .security import redactor
            QMessageBox.warning(self,'Diagnoseexport',redactor.text(str(exc)));return
        if not records:
            QMessageBox.information(self,'Diagnose',self.diagnostics.last_error or 'Keine aufgezeichneten Diagnosen in dieser Session. Diagnostic Trace vor dem Test unter Einstellungen / Debug einschalten.');return
        name='diagnostic_bundle_'+datetime.now().strftime('%Y%m%d_%H%M%S_%f')+'.zip'
        destination,_=QFileDialog.getSaveFileName(self,'Diagnostic Bundle exportieren',str(self.directory/name),'ZIP (*.zip)')
        if not destination:return
        try:
            self.diagnostics.export(destination,self.event_log.session_id,last_only)
            self.status.setText('Diagnose exportiert: '+destination)
        except Exception as exc:QMessageBox.warning(self,'Diagnoseexport',str(exc))

    def on_permission(self, request):
        worker = self.worker
        if not worker or worker.stopped.is_set():
            return
        self.status.setText("Warte auf Erlaubnis: " + request["path"])
        self.permission_dialog = PermissionDialog(request, self)
        self.permission_dialog.setModal(False)
        self.permission_dialog.finished.connect(lambda _result: self.permission_closed(worker))
        self.permission_dialog.show()

    def permission_closed(self, worker):
        dialog = self.permission_dialog
        self.permission_dialog = None
        if dialog:
            worker.resolve_permission(dialog.decision)
            dialog.deleteLater()

    def worker_finished(self):
        if self.permission_dialog:
            self.permission_dialog.reject()
        worker, self.worker = self.worker, None
        if worker and worker.agent:
            identifiers = getattr(worker.agent, 'experience_ids', [])
            self.last_experience_id = identifiers[-1] if identifiers else None
        self.set_busy(False)
        if worker and not worker.agent and worker.stopped.is_set():
            self.connection.setText('Verbindungsprüfung abgebrochen · erneut testen möglich')
        if not self.status.text().startswith('Fehler:'):
            self.status.setText("Bereit · Aufgabe beendet")
        self.refresh_memory()
        self.refresh_files()
        self.experience_panel.refresh()
        self.input.setFocus()
        if worker:
            worker.deleteLater()
        if self.closing:
            self.close()

    def response_timing(self,worker):
        if not worker.agent:return None
        import time
        timing={'total_seconds':round(time.monotonic()-worker.accepted_at,4)}
        resources=getattr(worker.agent,'resources',None)
        if resources:
            counters=resources.counters
            timing['model_calls']=counters.model_calls
            self.last_response_timing=timing
        return timing

    def on_failure(self, text):
        self.append_chat("Fehler", text, self.response_timing(self.worker) if self.worker else None)
        self.status.setText("Fehler: " + text)
        if self.worker and not self.worker.agent:
            self.connection.setText("Nicht verbunden · " + text)

    def on_event(self, event):
        details, kind = event["details"], event["event_type"]
        time = datetime.fromisoformat(event["timestamp"]).astimezone().strftime("%H:%M:%S")
        description = details.get("status") or details.get("path") or details.get("name") or details.get("reason") or ""
        self.events.append(event)
        self.activity_list.addItem(f"{time}  {LABELS.get(kind, kind)}  {description}")
        if len(self.events) > 500:
            self.events.pop(0)
            self.activity_list.takeItem(0)
        self.activity_list.scrollToBottom()
        if kind == "SEMANTIC_RESOLVER":
            self.semantic_status.setVisible(True);self.semantic_status.setText(details.get("status",""))
        if kind == "AGENT_STEP":
            self.status.setText(details.get("status", "Agent arbeitet"))
        if kind == 'BENCHMARK_PROGRESS':
            self.status.setText(details.get('status', 'Benchmark arbeitet'))
            self.experience_panel.progress.setText(details.get('status','Grundausbildung arbeitet'))
        if kind=='EXPERIENCE_RECORDED' and details.get('benchmark_id'):
            self.status.setText(details.get('status','Ergebnis gespeichert'))
            # Preserve the completed-count / measured ETA line from BENCHMARK_PROGRESS.
        if kind in {'VALIDATION_RESULT','FAILURE_CLASSIFIED','RESOURCE_OUTPUT_LIMIT','RESOURCE_RETRY_STARTED','RESOURCE_RETRY_SUCCESS','RESOURCE_RETRY_FAILED'}:
            self.status.setText(details.get('status',kind))
        if kind=='USER_MESSAGE':self.verification_label.setText('Verification: NOT_RUN')
        if kind=='VERIFICATION_COMPLETED':
            self.verification_label.setText('Verification: '+details['verification_status']+'\n'+details.get('message',''))
        if kind=='PROJECT_CHECK_COMPLETED':
            checks=', '.join(r['verifier_type']+': '+r['verification_status'] for r in details['verification_results']) or 'UNAVAILABLE'
            self.verification_label.setText('Verification: '+checks+'\nVerifizierte Findings: '+str(len(details['findings']))+' · Untersucht: '+str(len(details['inspected_files']))+' Dateien · bounded')
        if kind in {'FOLLOWUP_RESOLVED','FOLLOWUP_UNRESOLVED'}:self.active_model.setText('Controller · '+details['status'])
        if kind == "CONTROLLER_ACTION":
            self.active_model.setText("Controller · " + details.get("status", "Dateiaktion ohne Modell"))
        if kind in {"MODEL_ROUTED", "MODEL_ESCALATED"}:
            identifier = details.get("to_model", "")
            profile = self.registry.profiles.get(identifier) if self.registry else None
            self.active_model.setText(f"Arbeitendes Modell: {profile.display_name if profile else identifier} · {details.get('reason', '')}")
        if kind in {"FILE_READ", "FILE_WRITE"}:
            path = details.get("path", "")
            for item in self.recent.findItems(path, Qt.MatchFlag.MatchExactly):
                self.recent.takeItem(self.recent.row(item))
            self.recent.insertItem(0, path)
            while self.recent.count() > 10:
                self.recent.takeItem(10)
        c = event.get("resources")
        if c:
            self.resources.setText(f"Modell: {c['model_calls']} · Tools: {c['tool_calls']} · Lesen: {c['file_reads']} / {c['bytes_read']} Bytes · Schreiben: {c['file_writes']} · Memory: {c['memory_reads']} lesen / {c['memory_writes']} schreiben · Cache: {c['cache_hits']} · Kontext: {c['context_size']} Bytes · Tokens ≈ {c['input_tokens_estimate']} ein / {c['output_tokens_estimate']} aus · {c['elapsed_time']:.1f} s")
            if c.get("per_model"):
                self.per_model_text.setText(" · ".join(f"{key}: {value['model_calls']} Aufrufe / {value['elapsed_time']:.1f} s / {value['successful_tasks']} beendet / {value['failed_tasks']} fehlgeschlagen / {value.get('resource_retries',0)} Resource-Retry" for key, value in c["per_model"].items()))
                self.per_model_text.setToolTip(json.dumps(c["per_model"], ensure_ascii=False, indent=2))
                self.per_model_text.setVisible(True)
                self.details_toggle.setChecked(True)
            else:
                self.per_model_text.setVisible(False)

    def show_event_details(self, row):
        if 0 <= row < len(self.events):
            self.event_details.setPlainText(json.dumps(self.events[row], ensure_ascii=False, indent=2))

    def refresh_models(self):
        if self.worker:
            return
        try:
            if self.settings.models:
                self.registry = ModelRegistry.from_settings(self.settings)
                backend = RegistryProbe(self.registry)
            else:
                self.registry = None
                backend = create_backend(self.settings.model)
            self.connection.setText("Verbindung wird geprüft …")
            self.bind_worker(AgentWorker(backend=backend, log=self.event_log))
        except Exception as exc:
            self.connection.setText("Nicht verbunden · " + str(exc))

    def on_models(self, result):
        if self.settings.models:
            self.model.blockSignals(True)
            self.model.clear()
            self.model.addItem("AUTO", "")
            for profile in self.settings.models:
                if profile.enabled:
                    suffix = " (nicht verfügbar)" if self.registry.availability.get(profile.id) is False else ""
                    self.model.addItem(profile.display_name + suffix, profile.id)
            if self.settings.routing.mode == "manual" and self.model.findData(self.settings.routing.manual_model) >= 0:
                self.model.setCurrentIndex(self.model.findData(self.settings.routing.manual_model))
            self.model.blockSignals(False)
            available = len(self.registry.enabled())
            configured = sum(p.enabled for p in self.settings.models)
            errors = [f"{p.display_name}: {self.registry.errors.get(p.id, '')}" for p in self.settings.models if p.enabled and self.registry.availability.get(p.id) is False]
            self.connection.setText(f"Modellprofile: {available} von {configured} verfügbar · AUTO oder ein festes Modell auswählen" + ("\n" + " · ".join(errors) if errors else ""))
            return
        self.model.blockSignals(True)
        self.model.clear()
        self.model.addItems(result["models"])
        if self.settings.model.model in result["models"]:
            self.model.setCurrentText(self.settings.model.model)
        self.model.blockSignals(False)
        self.model_changed(self.model.currentText())
        provider = {"mock": "Demo / MockBackend (keine KI)", "ollama": "Ollama", "lmstudio": "LM Studio"}[self.settings.model.provider]
        caps = result["capabilities"]
        tool = "ja" if caps["tool_calling"] is True else "JSON-Kompatibilitätsmodus" if caps.get("tool_mode") == "json" else "nein" if caps["tool_calling"] is False else "unbekannt"
        structured = "ja" if caps["structured_output"] is True else "nein" if caps["structured_output"] is False else "unbekannt"
        state = "Verbunden" if result["models"] else "Server erreichbar, keine Modelle verfügbar"
        self.connection.setText(f"{provider} · {state} · Tools: {tool} · Structured Output: {structured} · Kontextlänge: {caps['context_length'] or 'unbekannt'}")

    def model_changed(self, model):
        if self.settings.models:
            self.settings.routing.manual_model = self.model.currentData() or ""
            self.settings.routing.mode = "manual" if self.settings.routing.manual_model else "auto"
            self.save_settings()
            return
        self.settings.model.model = model
        self.save_settings()

    def save_settings(self):
        try:
            self.settings.save(self.directory / "config.toml")
        except Exception as exc:
            self.status.setText("Einstellungen konnten nicht gespeichert werden: " + str(exc))

    def open_settings(self):
        if self.worker:
            return
        dialog = SettingsDialog(self.settings, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.settings = dialog.settings
            for widget,key in ((self.models_button,'MODELS'),(self.settings_button,'SETTINGS'),(self.project_button,'PROJECT_OPEN')):
                widget.setText(localize(key,self.settings.ui.language))
            self.semantic_status.setVisible(self.settings.semantic_resolver.enabled)
            self.semantic_status.setText("Semantic Intent Resolver: konfiguriert; Verfügbarkeit wird bei Bedarf geprüft" if self.settings.semantic_resolver.model else "Semantic resolver model is not available.")
            self.permissions = PermissionManager(self.settings.permissions)
            self.save_settings()
            self.apply_style()
            self.refresh_models()

    def open_models(self):
        if self.worker:
            return
        dialog = ModelsDialog(self.settings, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.settings = dialog.settings
            self.permissions = PermissionManager(self.settings.permissions)
            self.save_settings()
            self.refresh_models()

    def clear_session(self):
        if self.worker:
            return
        self.session.clear()
        self.last_experience_id = None
        for b in self.feedback_buttons:
            b.setEnabled(False)
        self.permissions = PermissionManager(self.settings.permissions)
        self.event_log = EventLog(self.directory / "logs", self.event_log.project_id)
        self.chat.clear()
        self.append_chat("AlienApe", "Session und temporäre Freigaben geleert. Gespeicherte Projektinformationen bleiben in SQLite verfügbar.")

    def rate_answer(self, success):
        if self.worker or self.last_experience_id is None:
            return
        identifier = self.experience.feedback(self.last_experience_id, success)
        self.on_event(self.event_log.write('EXPERIENCE_RECORDED', {'id': identifier, 'source': 'feedback',
            'signal': 'USER_CONFIRMED_SUCCESS' if success else 'USER_CONFIRMED_FAILURE'}))
        self.on_event(self.event_log.write('EXPERIENCE_AGGREGATED', {'source': 'feedback'}))
        self.status.setText('Bewertung gespeichert. Eine erneute Bewertung ersetzt deine vorige Bewertung dieses Versuchs.')
        self.experience_panel.refresh()

    def refresh_memory(self):
        selected = self.selected_memory() if hasattr(self, "memory_records") else None
        selected_id = selected["id"] if selected else None
        self.memory_table.blockSignals(True)
        self.memory_records = self.memory.all() if self.memory else []
        self.memory_table.setRowCount(len(self.memory_records))
        for row, record in enumerate(self.memory_records):
            state = "ersetzt" if record["superseded"] else "aktiv" if record["active"] else "inaktiv"
            if record["important"]:
                state += " · wichtig"
            values = [record["type"], record["logical_key"], record["content"][:160], record["source"], record["last_accessed"] or "—", str(record["access_count"]), state]
            for col, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setToolTip(record["content"] if col == 2 else value)
                self.memory_table.setItem(row, col, item)
        self.memory_table.clearSelection()
        self.memory_table.setCurrentCell(-1, -1)
        for row, record in enumerate(self.memory_records):
            if record["id"] == selected_id:
                self.memory_table.selectRow(row)
                break
        self.memory_table.blockSignals(False)
        self.update_memory_actions()

    def update_memory_actions(self):
        busy = bool(self.worker)
        self.memory_buttons[0].setEnabled(not busy and self.memory is not None)
        for widget in self.memory_buttons[1:]:
            widget.setEnabled(not busy and self.selected_memory() is not None)

    def selected_memory(self):
        row = self.memory_table.currentRow()
        records = getattr(self, "memory_records", [])
        return records[row] if self.memory_table.selectedItems() and 0 <= row < len(records) else None

    def memory_event(self, details):
        self.on_event(self.event_log.write("MEMORY_STORE", details))
        self.refresh_memory()

    def edit_memory(self, editing):
        if not self.memory or self.worker:
            return
        record = self.selected_memory() if editing else None
        if editing and not record:
            return
        dialog = MemoryDialog(record, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            try:
                result = self.memory.put(dialog.key.text(), dialog.kind.currentText(), dialog.content.toPlainText(), "Memory-Ansicht")
                if record and record["logical_key"] != dialog.key.text().strip().casefold():
                    self.memory.update_flags(record["id"], active=False)
                self.memory_event({"action": "edit" if editing else "create", "id": result["id"]})
                if result.get("superseded_ids"):
                    self.on_event(self.event_log.write("MEMORY_SUPERSEDE", {"ids": result["superseded_ids"]}))
            except Exception as exc:
                QMessageBox.warning(self, "Memory", str(exc))

    def toggle_memory(self):
        record = self.selected_memory()
        if record:
            self.memory.update_flags(record["id"], active=not record["active"])
            self.memory_event({"action": "toggle_active", "id": record["id"]})

    def important_memory(self):
        record = self.selected_memory()
        if record:
            self.memory.update_flags(record["id"], important=not record["important"])
            self.memory_event({"action": "toggle_important", "id": record["id"]})

    def delete_memory(self):
        record = self.selected_memory()
        if record and QMessageBox.question(self, "Memory löschen", f"Eintrag „{record['logical_key']}“ dauerhaft löschen?", QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No) == QMessageBox.StandardButton.Yes:
            self.memory.delete(record["id"])
            self.memory_event({"action": "delete", "id": record["id"]})

    def closeEvent(self, event):
        if self.worker:
            self.closing = True
            self.stop()
            event.ignore()
        else:
            event.accept()
