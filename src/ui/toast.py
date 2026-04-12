import shiboken6 as shiboken
from PySide6.QtWidgets import QFrame, QLabel, QHBoxLayout
from PySide6.QtCore import Qt, QTimer


class Toast(QFrame):
    """Notificacion toast flotante / Floating toast notification.
    Aparece en la esquina inferior derecha del host y se auto-elimina.
    Appears at the bottom-right of the host and auto-removes itself."""

    _STYLES = {
        'success': ('#0d3b1e', '#27ae60', '#2ecc71', 'e280'),
        'error':   ('#3b0d0d', '#e74c3c', '#ff6b6b', 'e281'),
        'info':    ('#0d1f3b', '#1f6aa5', '#5dade2', 'e282'),
        'warning': ('#3b2f0d', '#f39c12', '#f9a825', 'e283'),
    }
    _ICONS = {
        'success': 'e2 9c 85', 'error': 'e2 9d 8c',
        'info':    'e2 84 b9', 'warning': 'e2 9a a0',
    }

    def __init__(self, message: str, kind: str = 'info', duration: int = 3000, parent=None):
        super().__init__(parent)
        bg, border, fg, _ = self._STYLES.get(kind, self._STYLES['info'])
        icons = {'success': '✅', 'error': '❌', 'info': 'ℹ️', 'warning': '⚠️'}
        icon = icons.get(kind, 'ℹ️')

        self.setObjectName("Toast")
        self.setMinimumWidth(260)
        self.setMaximumWidth(480)
        self.setStyleSheet(f"""
            QFrame#Toast {{
                background-color: {bg};
                border: 1px solid {border};
                border-radius: 10px;
            }}
            QLabel {{ color: {fg}; font-size: 13px; }}
        """)
        hl = QHBoxLayout(self)
        hl.setContentsMargins(14, 10, 14, 10)
        hl.setSpacing(8)

        lbl_icon = QLabel(icon)
        lbl_icon.setStyleSheet(f"font-size: 18px; color: {fg};")
        hl.addWidget(lbl_icon)

        lbl_msg = QLabel(message)
        lbl_msg.setWordWrap(True)
        lbl_msg.setStyleSheet(f"color: {fg}; font-size: 13px;")
        hl.addWidget(lbl_msg, 1)

        self.adjustSize()
        if duration > 0:
            QTimer.singleShot(duration, self._auto_close)

    def _auto_close(self):
        if shiboken.isValid(self):
            self.deleteLater()
