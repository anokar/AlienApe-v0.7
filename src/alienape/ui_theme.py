"""Presentation only: shared palette and standard Qt display widgets."""
from PyQt6.QtWidgets import QLabel

def palette(high_contrast=False):
    if high_contrast:
        return dict(bg='#000000',panel='#000000',raised='#000000',fg='#ffffff',muted='#e0e0e0',border='#ffffff',accent='#ffff00',danger='#ffb4b4',user='#161616',error='#240000')
    return dict(bg='#1e2024',panel='#23262b',raised='#2b2f35',fg='#e0e2e6',muted='#a7adb7',border='#3b4048',accent='#82b2f5',danger='#e5a3a3',user='#1e2024',error='#292326')

class DisplayLabel(QLabel):
    """Same supplied text, bounded layout, full text available as tooltip."""
    def __init__(self,text='',parent=None):
        super().__init__(parent)
        self.setWordWrap(True)
        self.setText(text)
    def setText(self,text):
        super().setText(text)
        self.setToolTip(text)
        # Color only; never changes the supplied state/message or controller data.
        first=str(text).split('\n',1)[0].upper()
        tone='error' if any(x in first for x in ('FAIL','ERROR','FEHLER:')) else 'ok' if 'PASS' in first else 'neutral'
        self.setProperty('tone',tone)
        self.style().unpolish(self);self.style().polish(self)

def stylesheet(colors,padding):
    c=colors
    return f'''
        QWidget {{ background: {c['bg']}; color: {c['fg']}; }}
        QFrame#surface {{ background: {c['panel']}; border: none; border-right: 1px solid {c['border']}; }}
        QFrame#surface > QWidget {{ background: transparent; }}
        QFrame#statusbar {{ border: none; border-top: 1px solid {c['border']}; }}
        QFrame#separator {{ background: {c['border']}; max-height: 1px; }}
        QLabel {{ background: transparent; }}
        QLabel#brand {{ font-size: 18px; font-weight: 600; }}
        QLabel#muted, QLabel#eyebrow {{ color: {c['muted']}; }}
        QLabel#eyebrow {{ font-size: 11px; font-weight: 600; padding: 3px 0; }}
        QLabel[tone="error"] {{ color: {c['danger']}; }}
        QLabel[tone="ok"] {{ color: {c['fg']}; }}
        QLineEdit, QPlainTextEdit, QTextBrowser, QTreeWidget, QListWidget, QTableWidget, QComboBox, QSpinBox, QDoubleSpinBox {{
            background: {c['panel']}; border: 1px solid {c['border']}; border-radius: 3px; padding: 4px;
            selection-background-color: {c['border']}; selection-color: {c['fg']}; }}
        QLineEdit:read-only {{ color: {c['muted']}; }}
        QTreeWidget#explorer, QListWidget#recent {{ background: {c['panel']}; border: none; padding: 0; }}
        QPlainTextEdit#technical {{ font-family: Consolas; }}
        QTextBrowser#conversation {{ border: none; padding: 8px 12px; background: {c['bg']}; }}
        QPushButton, QToolButton {{ background: {c['raised']}; border: 1px solid {c['border']}; border-radius: 3px; padding: {padding}px 10px; }}
        QPushButton:hover, QToolButton:hover {{ border-color: {c['accent']}; }}
        QPushButton:disabled, QToolButton:disabled {{ color: {c['muted']}; border-color: {c['border']}; }}
        QPushButton:focus, QToolButton:focus, QComboBox:focus, QPlainTextEdit:focus, QLineEdit:focus, QTreeWidget:focus, QListWidget:focus, QTableWidget:focus {{ border: 2px solid {c['accent']}; }}
        QPushButton#primary {{ background: {c['accent']}; color: {c['bg']}; font-weight: 600; }}
        QPushButton#primary:disabled {{ background: {c['raised']}; color: {c['muted']}; }}
        QPushButton#stop:enabled {{ color: {c['danger']}; }}
        QPushButton#quickAction {{ background: transparent; border: none; color: {c['muted']}; padding: 4px 8px; }}
        QToolButton#disclosure {{ text-align: left; background: transparent; border: none; color: {c['muted']}; padding: 4px 0; }}
        QTabWidget::pane {{ border: none; }}
        QTabBar::tab {{ background: transparent; color: {c['muted']}; padding: 7px 12px; margin-right: 2px; border-bottom: 2px solid transparent; }}
        QTabBar::tab:selected {{ color: {c['fg']}; border-bottom-color: {c['accent']}; }}
        QHeaderView::section {{ background: {c['raised']}; color: {c['muted']}; padding: 5px; border: none; border-bottom: 1px solid {c['border']}; }}
        QTreeWidget::item, QListWidget::item {{ padding: 3px 2px; }}
        QSplitter::handle {{ background: {c['bg']}; width: 4px; }}
        QScrollArea {{ border: none; background: transparent; }}
        QToolTip {{ background: {c['raised']}; color: {c['fg']}; border: 1px solid {c['border']}; padding: 6px; }}
    '''
