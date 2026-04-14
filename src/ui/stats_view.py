from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, QFrame,
                             QGridLayout, QScrollArea)
from PySide6.QtCore import Qt
from src.db.models import Multimedia
from peewee import fn
import logging

class StatBox(QFrame):
    def __init__(self, title, value, icon=""):
        super().__init__()
        self.setFixedSize(220, 150)
        self.setObjectName("StatBox")
        self.setStyleSheet("""
            QFrame#StatBox {
                background-color: #1a1a1a;
                border-radius: 15px;
                border: 1px solid #333;
            }
            QFrame#StatBox:hover {
                border: 1px solid #1f6aa5;
                background-color: #222;
            }
        """)

        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignCenter)
        layout.setSpacing(4)

        # D2 FIX: Mostrar el icono si se proporciona / Show icon if provided
        if icon:
            self.lbl_icon = QLabel(icon)
            self.lbl_icon.setStyleSheet("font-size: 26px;")
            self.lbl_icon.setAlignment(Qt.AlignCenter)
            layout.addWidget(self.lbl_icon)

        self.lbl_value = QLabel(str(value))
        self.lbl_value.setStyleSheet("font-size: 30px; font-weight: bold; color: #1f6aa5;")
        self.lbl_value.setAlignment(Qt.AlignCenter)

        self.lbl_title = QLabel(title)
        self.lbl_title.setStyleSheet("font-size: 13px; color: #888; font-weight: 500;")
        self.lbl_title.setAlignment(Qt.AlignCenter)

        layout.addWidget(self.lbl_value)
        layout.addWidget(self.lbl_title)

class StatsView(QWidget):
    def __init__(self, engine):
        super().__init__()
        self.engine = engine
        self.stat_widgets = []
        self.setup_ui()

    def setup_ui(self):
        self.main_layout = QVBoxLayout(self)
        self.main_layout.setContentsMargins(0, 0, 0, 0)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setStyleSheet("background: transparent; border: none;")
        
        self.container = QWidget()
        self.content_layout = QVBoxLayout(self.container)
        self.content_layout.setContentsMargins(40, 40, 40, 40)
        self.content_layout.setSpacing(30)

        header = QLabel(self.engine.config.tr('lbl_stats_title', '📊 Mi Perfil Cinéfilo'))
        header.setStyleSheet("font-size: 32px; font-weight: bold; color: #fff;")
        self.content_layout.addWidget(header)

        # Grid de Estadísticas Principales
        self.stats_grid_container = QWidget()
        self.stats_grid = QGridLayout(self.stats_grid_container)
        self.stats_grid.setSpacing(25)
        self.stats_grid.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        self.content_layout.addWidget(self.stats_grid_container)
        
        # Sección: Géneros Favoritos
        self.content_layout.addWidget(QLabel(self.engine.config.tr('lbl_genres', '🎭 Géneros que más consumes / Top Genres'), objectName="SectionTitle"))
        self.genre_container = QFrame()
        self.genre_container.setStyleSheet("background-color: #161616; border-radius: 15px; padding: 20px;")
        self.genre_layout = QVBoxLayout(self.genre_container)
        self.content_layout.addWidget(self.genre_container)

        self.content_layout.addStretch()
        
        self.scroll.setWidget(self.container)
        self.main_layout.addWidget(self.scroll)
        
        self.setStyleSheet("""
            QLabel#SectionTitle { font-size: 20px; font-weight: bold; color: #1f6aa5; margin-top: 10px; }
        """)

    def resizeEvent(self, event):
        """Reajustar el grid de estadísticas al redimensionar (v2.11.5)"""
        super().resizeEvent(event)
        self.adjust_stats_grid()

    def adjust_stats_grid(self):
        """Calcula cuántas cajas de estadísticas caben por fila."""
        if not self.stat_widgets: return
        
        width = self.scroll.viewport().width() - 80 # Margenes
        box_width = 220 + 25 # Ancho caja + spacing
        columns = max(1, width // box_width)
        
        for idx, widget in enumerate(self.stat_widgets):
            self.stats_grid.addWidget(widget, idx // columns, idx % columns)

    def refresh_stats(self):
        # Limpiar grid de forma segura (v2.11.14)
        for i in reversed(range(self.stats_grid.count())): 
            widget = self.stats_grid.itemAt(i).widget()
            if widget:
                widget.setParent(None)
                widget.deleteLater()
        
        self.stat_widgets = []
        
        # Limpiar géneros de forma segura
        for i in reversed(range(self.genre_layout.count())):
            widget = self.genre_layout.itemAt(i).widget()
            if widget:
                widget.setParent(None)
                widget.deleteLater()

        try:
            # 1. Estadísticas Básicas
            total_count = Multimedia.select().count()
            seen_count = Multimedia.select().where(Multimedia.estado_visto == 1).count()
            
            # Suma de tiempo (convertido a horas)
            total_mins = Multimedia.select(fn.SUM(Multimedia.minuto_actual)).scalar() or 0.0
            total_hrs = total_mins / 60.0
            
            # Nota promedio
            avg_rating = Multimedia.select(fn.AVG(Multimedia.calificacion_personal)).where(Multimedia.calificacion_personal > 0).scalar() or 0.0

            self.stat_widgets = [
                StatBox(self.engine.config.tr('lbl_stats_saved', 'Títulos Guardados'), total_count, "🎬"),
                StatBox(self.engine.config.tr('lbl_stats_seen',  'Títulos Vistos'),     seen_count,  "👁️"),
                StatBox(self.engine.config.tr('lbl_stats_hours', 'Horas Invertidas'),   f"{total_hrs:.1f}h", "⏱️"),
                StatBox(self.engine.config.tr('lbl_stats_avg',   'Nota Promedio'),      f"{avg_rating:.1f}★", "⭐"),
            ]

            self.adjust_stats_grid()

            # 2. Análisis de Géneros
            all_genres = []
            for m in Multimedia.select(Multimedia.generos).where(Multimedia.generos.is_null(False)):
                all_genres.extend([g.strip() for g in m.generos.split(',') if g.strip()])
            
            from collections import Counter
            counts = Counter(all_genres).most_common(5)
            
            if not counts:
                self.genre_layout.addWidget(QLabel(self.engine.config.tr('msg_no_genres', "Aún no hay suficientes datos de géneros.")))
            else:
                for genre, count in counts:
                    row = QWidget()
                    row_l = QHBoxLayout(row)
                    row_l.setContentsMargins(0, 5, 0, 5)
                    
                    name_lbl = QLabel(genre)
                    name_lbl.setStyleSheet("font-weight: bold; font-size: 15px;")
                    
                    count_text = f"{count} {self.engine.config.tr('lbl_titles', 'títulos')}"
                    count_lbl = QLabel(count_text)
                    count_lbl.setStyleSheet("color: #888;")
                    
                    # Barra de progreso visual para el género
                    progress = QFrame()
                    max_count = counts[0][1]
                    width = (count / max_count) * 200
                    progress.setFixedSize(int(width), 8)
                    progress.setStyleSheet("background-color: #1f6aa5; border-radius: 4px;")
                    
                    row_l.addWidget(name_lbl, 2)
                    row_l.addWidget(progress, 3)
                    row_l.addWidget(count_lbl, 1)
                    self.genre_layout.addWidget(row)

        except Exception as e:
            logging.error("Error cargando estadísticas: %s", e)
