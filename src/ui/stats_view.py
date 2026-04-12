from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, QFrame,
                             QGridLayout, QScrollArea, QProgressBar)
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

        # F7: Sección Progreso de Series / Series Completion
        self.content_layout.addWidget(QLabel(
            self.engine.config.tr('lbl_series_completion', '📺 Progreso de Series / Series Progress'),
            objectName="SectionTitle"
        ))
        self.series_progress_container = QFrame()
        self.series_progress_container.setStyleSheet("background-color: #161616; border-radius: 15px; padding: 20px;")
        self.series_progress_layout = QVBoxLayout(self.series_progress_container)
        self.content_layout.addWidget(self.series_progress_container)

        # F7: Sección Actividad Mensual / Monthly Activity
        self.content_layout.addWidget(QLabel(
            self.engine.config.tr('lbl_monthly_activity', '📅 Actividad Mensual / Monthly Activity'),
            objectName="SectionTitle"
        ))
        self.monthly_container = QFrame()
        self.monthly_container.setStyleSheet("background-color: #161616; border-radius: 15px; padding: 20px;")
        self.monthly_layout = QVBoxLayout(self.monthly_container)
        self.content_layout.addWidget(self.monthly_container)

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

        # F7: Limpiar secciones extendidas / Clear extended sections
        for layout in (getattr(self, 'series_progress_layout', None),
                       getattr(self, 'monthly_layout', None)):
            if layout is None:
                continue
            for i in reversed(range(layout.count())):
                w = layout.itemAt(i).widget()
                if w:
                    w.setParent(None)
                    w.deleteLater()

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

            # F7: Progreso de series / Series completion
            try:
                serie_rows = (
                    Multimedia
                    .select(
                        Multimedia.titulo,
                        fn.COUNT(Multimedia.id).alias('total'),
                        fn.SUM(Multimedia.estado_visto).alias('seen'),
                    )
                    .where(Multimedia.tipo_contenido == 'Serie')
                    .group_by(Multimedia.titulo)
                    .order_by(fn.COUNT(Multimedia.id).desc())
                    .limit(8)
                )
                serie_list = list(serie_rows)
                if not serie_list:
                    self.series_progress_layout.addWidget(QLabel(
                        self.engine.config.tr('msg_no_series', 'No hay series guardadas aún.')
                    ))
                else:
                    for r in serie_list:
                        total = int(r.total or 0)
                        seen = int(r.seen or 0)
                        if total == 0:
                            continue
                        pct = int((seen / total) * 100)
                        row_w = QWidget()
                        row_l = QHBoxLayout(row_w)
                        row_l.setContentsMargins(0, 4, 0, 4)
                        lbl_t = QLabel(r.titulo or "—")
                        lbl_t.setStyleSheet("font-size: 13px; font-weight: bold;")
                        lbl_t.setFixedWidth(220)
                        pb = QProgressBar()
                        pb.setRange(0, 100)
                        pb.setValue(pct)
                        pb.setFixedHeight(10)
                        pb.setTextVisible(False)
                        color = "#27ae60" if seen >= total else "#1f6aa5"
                        pb.setStyleSheet(f"""
                            QProgressBar {{ background: #2a2a2a; border-radius: 5px; border: none; }}
                            QProgressBar::chunk {{ background: {color}; border-radius: 5px; }}
                        """)
                        lbl_pct = QLabel(f"{seen}/{total}")
                        lbl_pct.setStyleSheet("color: #888; font-size: 12px;")
                        lbl_pct.setFixedWidth(55)
                        row_l.addWidget(lbl_t)
                        row_l.addWidget(pb, 1)
                        row_l.addWidget(lbl_pct)
                        self.series_progress_layout.addWidget(row_w)
            except Exception as _e:
                logging.debug("F7 series progress: %s", _e)

            # F7: Actividad mensual / Monthly activity
            try:
                from collections import defaultdict
                import datetime
                monthly: dict = defaultdict(int)
                for m in Multimedia.select(Multimedia.ultima_actualizacion).where(
                    Multimedia.ultima_actualizacion.is_null(False)
                ):
                    raw = str(m.ultima_actualizacion or "")[:7]  # "YYYY-MM"
                    if raw and len(raw) == 7:
                        monthly[raw] += 1
                sorted_months = sorted(monthly.items())[-6:]  # últimos 6 meses
                if not sorted_months:
                    self.monthly_layout.addWidget(QLabel(
                        self.engine.config.tr('msg_no_activity', 'Sin actividad registrada.')
                    ))
                else:
                    max_v = max(v for _, v in sorted_months) or 1
                    for month, cnt in sorted_months:
                        pct = int((cnt / max_v) * 100)
                        row_w = QWidget()
                        row_l = QHBoxLayout(row_w)
                        row_l.setContentsMargins(0, 3, 0, 3)
                        lbl_m = QLabel(month)
                        lbl_m.setStyleSheet("font-size: 12px; color: #9aa0a6;")
                        lbl_m.setFixedWidth(70)
                        pb = QProgressBar()
                        pb.setRange(0, 100)
                        pb.setValue(pct)
                        pb.setFixedHeight(10)
                        pb.setTextVisible(False)
                        pb.setStyleSheet("""
                            QProgressBar { background: #2a2a2a; border-radius: 5px; border: none; }
                            QProgressBar::chunk { background: #8e44ad; border-radius: 5px; }
                        """)
                        lbl_cnt = QLabel(str(cnt))
                        lbl_cnt.setStyleSheet("color: #888; font-size: 12px;")
                        lbl_cnt.setFixedWidth(40)
                        row_l.addWidget(lbl_m)
                        row_l.addWidget(pb, 1)
                        row_l.addWidget(lbl_cnt)
                        self.monthly_layout.addWidget(row_w)
            except Exception as _e:
                logging.debug("F7 monthly: %s", _e)

        except Exception as e:
            logging.error("Error cargando estadísticas: %s", e)
