"""
themes.py — Temas visuales de VRCMT
Palettes inspirados en los más populares de la comunidad Qt/VSCode.
"""

# ── helpers internos ──────────────────────────────────────────────────────────

def _build(bg, bg2, bg3, fg, fg2, accent, accent2, border, scroll_h):
    """Genera un QSS completo a partir de los colores base del tema."""
    return f"""
QWidget {{ background-color: {bg}; color: {fg}; font-family: 'Segoe UI', Arial, sans-serif; }}
QScrollArea, QAbstractScrollArea {{ background-color: transparent; border: none; }}
QWidget#Sidebar {{ background-color: {bg2}; border-right: 1px solid {border}; }}
QPushButton#NavButton {{ background-color: transparent; color: {fg2}; border: none; padding: 12px 20px; text-align: left; font-size: 14px; border-radius: 8px; margin: 2px 8px; }}
QPushButton#NavButton:hover {{ background-color: {bg3}; color: {fg}; }}
QPushButton#NavButton:checked {{ background-color: {accent}; color: #fff; font-weight: bold; }}
QScrollBar:vertical {{ background: {bg2}; width: 6px; border-radius: 3px; }}
QScrollBar::handle:vertical {{ background: {scroll_h}; border-radius: 3px; min-height: 30px; }}
QScrollBar::handle:vertical:hover {{ background: {accent}; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0px; }}
QLineEdit {{ background-color: {bg2}; border: 1px solid {border}; border-radius: 8px; padding: 8px 12px; color: {fg}; font-size: 14px; }}
QLineEdit:focus {{ border: 1px solid {accent}; }}
QComboBox {{ background-color: {bg2}; border: 1px solid {border}; border-radius: 8px; padding: 8px 10px; color: {fg}; }}
QComboBox::drop-down {{ border: none; width: 24px; }}
QComboBox QAbstractItemView {{ background-color: {bg2}; border: 1px solid {border}; selection-background-color: {accent}; color: {fg}; }}
QFrame {{ border: none; }}
QLabel {{ background: transparent; }}
QCheckBox {{ color: {fg}; background: transparent; }}
QCheckBox::indicator {{ width: 16px; height: 16px; border-radius: 4px; border: 2px solid {border}; background: {bg2}; }}
QCheckBox::indicator:checked {{ background: {accent}; border-color: {accent}; }}
QPushButton {{ background-color: {accent}; color: #fff; border-radius: 8px; padding: 8px 16px; border: none; font-size: 13px; }}
QPushButton:hover {{ background-color: {accent2}; }}
QPushButton:pressed {{ background-color: {accent}; }}
QPushButton:disabled {{ background-color: {bg3}; color: {fg2}; }}
QFrame#MediaCard {{ background-color: {bg2}; border: 1px solid {border}; border-radius: 12px; }}
QFrame#MediaCard:hover {{ border: 1px solid {accent}; background-color: {bg3}; }}
QFrame#MediaListRow {{ background-color: {bg2}; border: 1px solid {border}; border-radius: 8px; }}
QFrame#MediaListRow:hover {{ background-color: {bg3}; border: 1px solid {accent}; }}
QProgressBar {{ background-color: {bg3}; border-radius: 4px; border: none; }}
QProgressBar::chunk {{ background-color: {accent}; border-radius: 4px; }}
QSlider::groove:horizontal {{ background: {bg3}; height: 4px; border-radius: 2px; }}
QSlider::handle:horizontal {{ background: {accent}; width: 14px; height: 14px; border-radius: 7px; margin: -5px 0; }}
QTabBar::tab {{ background: {bg2}; color: {fg2}; padding: 8px 16px; border-radius: 6px 6px 0 0; border: 1px solid {border}; margin-right: 2px; }}
QTabBar::tab:selected {{ background: {accent}; color: #fff; border-color: {accent}; }}
QTabBar::tab:hover {{ background: {bg3}; color: {fg}; }}
"""

# ── Temas definidos ───────────────────────────────────────────────────────────

# bg, bg2, bg3, fg, fg2, accent, accent2, border, scroll_h

DARK_THEME = _build(
    bg="#0d0d0d", bg2="#161616", bg3="#202020",
    fg="#f1f3f4", fg2="#9aa0a6",
    accent="#1f6aa5", accent2="#2980b9",
    border="#2a2a2a", scroll_h="#333"
)

AMOLED_THEME = _build(
    bg="#000000", bg2="#0a0a0a", bg3="#111111",
    fg="#f1f3f4", fg2="#9aa0a6",
    accent="#1f6aa5", accent2="#2980b9",
    border="#1a1a1a", scroll_h="#222"
)

NORD_THEME = _build(
    bg="#2E3440", bg2="#3B4252", bg3="#434C5E",
    fg="#ECEFF4", fg2="#D8DEE9",
    accent="#5E81AC", accent2="#81A1C1",
    border="#4C566A", scroll_h="#4C566A"
)

DRACULA_THEME = _build(
    bg="#282A36", bg2="#21222C", bg3="#373844",
    fg="#F8F8F2", fg2="#6272A4",
    accent="#BD93F9", accent2="#9580FF",
    border="#44475A", scroll_h="#44475A"
)

MONOKAI_THEME = _build(
    bg="#272822", bg2="#1e1f1c", bg3="#33342e",
    fg="#F8F8F2", fg2="#75715E",
    accent="#A6E22E", accent2="#66D9EF",
    border="#3E3D32", scroll_h="#49483E"
)

SOLARIZED_THEME = _build(
    bg="#002B36", bg2="#073642", bg3="#0D3A47",
    fg="#839496", fg2="#657B83",
    accent="#268BD2", accent2="#2AA198",
    border="#073642", scroll_h="#586E75"
)

OCEAN_THEME = _build(
    bg="#0A1628", bg2="#0F2240", bg3="#1A3A5C",
    fg="#BCCFE8", fg2="#7A9ECC",
    accent="#1E90FF", accent2="#00BFFF",
    border="#1A3A5C", scroll_h="#1E5080"
)

CARBON_THEME = _build(
    bg="#121212", bg2="#1E1E1E", bg3="#2C2C2C",
    fg="#E0E0E0", fg2="#9E9E9E",
    accent="#BB86FC", accent2="#CF6679",
    border="#383838", scroll_h="#424242"
)

CYBERPUNK_THEME = _build(
    bg="#0D0D0D", bg2="#130D21", bg3="#1E1032",
    fg="#E0F0FF", fg2="#8080AA",
    accent="#00FFDD", accent2="#FF00AA",
    border="#2A1050", scroll_h="#3A1070"
)

ROSEPINE_THEME = _build(
    bg="#191724", bg2="#1f1d2e", bg3="#26233a",
    fg="#e0def4", fg2="#908caa",
    accent="#c4a7e7", accent2="#ebbcba",
    border="#393552", scroll_h="#524f67"
)

BOSQUE_THEME = _build(
    bg="#0D1F12", bg2="#142A18", bg3="#1E3D24",
    fg="#D8EDD9", fg2="#7AAF83",
    accent="#4CAF50", accent2="#66BB6A",
    border="#2A5230", scroll_h="#2A5230"
)

SUNSET_THEME = _build(
    bg="#1A0A0A", bg2="#2A1010", bg3="#3A1818",
    fg="#FFE4CC", fg2="#CC8866",
    accent="#FF6B35", accent2="#FF8C42",
    border="#5A2020", scroll_h="#7A3030"
)

# ── Registro de temas ─────────────────────────────────────────────────────────

_THEMES = {
    "Oscuro":      DARK_THEME,       # 🌑 Clásico oscuro
    "AMOLED":      AMOLED_THEME,     # ⬛ Negro puro (OLED)
    "Nord":        NORD_THEME,       # 🧊 Azul nórdico
    "Dracula":     DRACULA_THEME,    # 🧛 Púrpura oscuro
    "Monokai":     MONOKAI_THEME,    # 🍃 Verde editor
    "Solarized":   SOLARIZED_THEME,  # 🌊 Azul verdoso
    "Ocean":       OCEAN_THEME,      # 🌊 Azul marino
    "Carbon":      CARBON_THEME,     # 💜 Material púrpura
    "Cyberpunk":   CYBERPUNK_THEME,  # 💚 Neón verde/cian
    "Rosé Pine":   ROSEPINE_THEME,   # 🌸 Rosado suave
    "Bosque":      BOSQUE_THEME,     # 🌲 Verde bosque
    "Sunset":      SUNSET_THEME,     # 🌅 Naranja atardecer
}

# Colores de acento para la vista previa en el selector (hex del accent)
THEME_ACCENTS = {
    "Oscuro":    "#1f6aa5",
    "AMOLED":    "#1f6aa5",
    "Nord":      "#5E81AC",
    "Dracula":   "#BD93F9",
    "Monokai":   "#A6E22E",
    "Solarized": "#268BD2",
    "Ocean":     "#1E90FF",
    "Carbon":    "#BB86FC",
    "Cyberpunk": "#00FFDD",
    "Rosé Pine": "#c4a7e7",
    "Bosque":    "#4CAF50",
    "Sunset":    "#FF6B35",
}


def get_theme(name: str) -> str:
    return _THEMES.get(name, DARK_THEME)


def theme_names() -> list:
    return list(_THEMES.keys())


def theme_accent(name: str) -> str:
    return THEME_ACCENTS.get(name, "#1f6aa5")
