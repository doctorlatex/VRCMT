DARK_THEME = """
QWidget { background-color: #0d0d0d; color: #f1f3f4; font-family: 'Segoe UI', Arial, sans-serif; }
QScrollArea, QAbstractScrollArea { background-color: transparent; border: none; }
QWidget#Sidebar { background-color: #111; border-right: 1px solid #222; }
QPushButton#NavButton { background-color: transparent; color: #9aa0a6; border: none; padding: 12px 20px; text-align: left; font-size: 14px; border-radius: 8px; margin: 2px 8px; }
QPushButton#NavButton:hover { background-color: #1a1a2e; color: #fff; }
QPushButton#NavButton:checked { background-color: #1f6aa5; color: #fff; font-weight: bold; }
QScrollBar:vertical { background: #111; width: 6px; border-radius: 3px; }
QScrollBar::handle:vertical { background: #333; border-radius: 3px; min-height: 30px; }
QScrollBar::handle:vertical:hover { background: #1f6aa5; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0px; }
QLineEdit { background-color: #1a1a1a; border: 1px solid #333; border-radius: 10px; padding: 8px 12px; color: #f1f3f4; font-size: 14px; }
QLineEdit:focus { border: 1px solid #1f6aa5; }
QComboBox { background-color: #1a1a1a; border: 1px solid #333; border-radius: 10px; padding: 8px 10px; color: #f1f3f4; }
QComboBox::drop-down { border: none; width: 24px; }
QComboBox QAbstractItemView { background-color: #111; border: 1px solid #333; selection-background-color: #1f6aa5; color: #f1f3f4; }
"""

AMOLED_THEME = """
QWidget { background-color: #000000; color: #f1f3f4; font-family: 'Segoe UI', Arial, sans-serif; }
QScrollArea, QAbstractScrollArea { background-color: transparent; border: none; }
QWidget#Sidebar { background-color: #000000; border-right: 1px solid #1a1a1a; }
QPushButton#NavButton { background-color: transparent; color: #9aa0a6; border: none; padding: 12px 20px; text-align: left; font-size: 14px; border-radius: 8px; margin: 2px 8px; }
QPushButton#NavButton:hover { background-color: #0a0a1e; color: #fff; }
QPushButton#NavButton:checked { background-color: #1f6aa5; color: #fff; font-weight: bold; }
QScrollBar:vertical { background: #000; width: 6px; border-radius: 3px; }
QScrollBar::handle:vertical { background: #222; border-radius: 3px; min-height: 30px; }
QScrollBar::handle:vertical:hover { background: #1f6aa5; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0px; }
QLineEdit { background-color: #0a0a0a; border: 1px solid #1a1a1a; border-radius: 10px; padding: 8px 12px; color: #f1f3f4; font-size: 14px; }
QLineEdit:focus { border: 1px solid #1f6aa5; }
QComboBox { background-color: #0a0a0a; border: 1px solid #1a1a1a; border-radius: 10px; padding: 8px 10px; color: #f1f3f4; }
QComboBox::drop-down { border: none; width: 24px; }
QComboBox QAbstractItemView { background-color: #000; border: 1px solid #1a1a1a; selection-background-color: #1f6aa5; color: #f1f3f4; }
QFrame#MediaCard { background-color: #050505; border: 1px solid #111; border-radius: 12px; }
QFrame#MediaCard:hover { border: 1px solid #1f6aa5; background-color: #090909; }
QFrame#MediaListRow { background-color: #050505; border: 1px solid #0a0a0a; border-radius: 8px; }
QFrame#MediaListRow:hover { background-color: #090909; border: 1px solid #1f6aa5; }
"""

_THEMES = {"Oscuro": DARK_THEME, "AMOLED": AMOLED_THEME}


def get_theme(name):
    return _THEMES.get(name, DARK_THEME)


def theme_names():
    return list(_THEMES.keys())
