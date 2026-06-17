"""PySide6 desktop app for System Info Checker."""

from __future__ import annotations

from pathlib import Path
import sys

from PySide6.QtCore import QThread, Qt, Signal
from PySide6.QtGui import QAction, QClipboard, QCloseEvent, QFont
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QStatusBar,
    QVBoxLayout,
    QWidget,
)

from . import __app_name__, __version__
from .collector import InfoItem, collect_system_info, format_report


class CollectorThread(QThread):
    """Collect system information without freezing the GUI."""

    finished_collecting = Signal(list)
    failed = Signal(str)

    def run(self) -> None:
        try:
            self.finished_collecting.emit(collect_system_info())
        except Exception as exc:  # pragma: no cover - last-resort GUI guard
            self.failed.emit(str(exc))


class InfoDashboard(QMainWindow):
    """Main application window."""

    def __init__(self) -> None:
        super().__init__()
        self.items: list[InfoItem] = []
        self.value_labels: list[QLabel] = []
        self.collector_thread: CollectorThread | None = None

        self.setWindowTitle(f"{__app_name__} {__version__}")
        self.resize(1080, 760)
        self.setMinimumSize(840, 560)

        self._build_actions()
        self._build_layout()
        self._apply_theme()
        self.refresh()

    def _build_actions(self) -> None:
        refresh_action = QAction("Refresh", self)
        refresh_action.triggered.connect(self.refresh)
        refresh_action.setShortcut("F5")
        self.addAction(refresh_action)

        copy_action = QAction("Copy All", self)
        copy_action.triggered.connect(self.copy_all)
        copy_action.setShortcut("Ctrl+C")
        self.addAction(copy_action)

        save_action = QAction("Save Report", self)
        save_action.triggered.connect(self.save_report)
        save_action.setShortcut("Ctrl+S")
        self.addAction(save_action)

    def _build_layout(self) -> None:
        root = QWidget()
        root_layout = QVBoxLayout(root)
        root_layout.setContentsMargins(24, 22, 24, 20)
        root_layout.setSpacing(18)

        header = QHBoxLayout()
        title_stack = QVBoxLayout()
        title_stack.setSpacing(4)

        title = QLabel(__app_name__)
        title.setObjectName("TitleLabel")
        subtitle = QLabel("Read-only Windows hardware and runtime information")
        subtitle.setObjectName("SubtitleLabel")

        title_stack.addWidget(title)
        title_stack.addWidget(subtitle)
        header.addLayout(title_stack, stretch=1)

        self.refresh_button = QPushButton("Refresh")
        self.refresh_button.clicked.connect(self.refresh)
        self.copy_button = QPushButton("Copy All")
        self.copy_button.clicked.connect(self.copy_all)
        self.save_button = QPushButton("Save Report")
        self.save_button.clicked.connect(self.save_report)

        for button in (self.refresh_button, self.copy_button, self.save_button):
            button.setCursor(Qt.PointingHandCursor)
            button.setMinimumHeight(38)
            header.addWidget(button)

        root_layout.addLayout(header)

        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setFrameShape(QFrame.NoFrame)

        self.content = QWidget()
        self.content_layout = QVBoxLayout(self.content)
        self.content_layout.setContentsMargins(0, 0, 0, 0)
        self.content_layout.setSpacing(14)
        self.scroll_area.setWidget(self.content)

        root_layout.addWidget(self.scroll_area, stretch=1)
        self.setCentralWidget(root)

        status = QStatusBar()
        status.showMessage("Ready")
        self.setStatusBar(status)

    def _apply_theme(self) -> None:
        app = QApplication.instance()
        if app:
            app.setStyle("Fusion")
            app.setFont(QFont("Segoe UI", 10))

        self.setStyleSheet(
            """
            QMainWindow, QWidget {
                background: #111318;
                color: #eef2f7;
            }
            #TitleLabel {
                font-size: 26px;
                font-weight: 700;
                color: #ffffff;
            }
            #SubtitleLabel {
                color: #aab4c2;
                font-size: 12px;
            }
            QScrollArea {
                background: transparent;
            }
            QGroupBox {
                border: 1px solid #293241;
                border-radius: 8px;
                margin-top: 18px;
                padding: 18px 14px 14px 14px;
                background: #171b22;
                font-weight: 700;
                color: #dce6f2;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 12px;
                padding: 0 8px;
                color: #81d4fa;
            }
            QLabel[role="field"] {
                color: #9aa6b5;
                font-weight: 600;
            }
            QLabel[role="value"] {
                color: #f5f8fb;
                background: #0d1016;
                border: 1px solid #252c38;
                border-radius: 6px;
                padding: 8px 10px;
                selection-background-color: #2374ab;
            }
            QPushButton {
                background: #1f6feb;
                color: #ffffff;
                border: none;
                border-radius: 7px;
                padding: 8px 14px;
                font-weight: 700;
            }
            QPushButton:hover {
                background: #2f81f7;
            }
            QPushButton:pressed {
                background: #1859bd;
            }
            QPushButton:disabled {
                background: #303846;
                color: #8894a4;
            }
            QStatusBar {
                background: #0d1016;
                color: #aab4c2;
                border-top: 1px solid #252c38;
            }
            """
        )

    def refresh(self) -> None:
        if self.collector_thread and self.collector_thread.isRunning():
            return

        self.statusBar().showMessage("Refreshing system information...")
        self._set_buttons_enabled(False)

        self.collector_thread = CollectorThread()
        self.collector_thread.finished_collecting.connect(self._refresh_complete)
        self.collector_thread.failed.connect(self._refresh_failed)
        self.collector_thread.start()

    def _refresh_complete(self, items: list[InfoItem]) -> None:
        self.items = items
        self._render_items()
        self._set_buttons_enabled(True)
        self.statusBar().showMessage("System information refreshed")

    def _refresh_failed(self, error: str) -> None:
        self._set_buttons_enabled(True)
        self.statusBar().showMessage("Refresh failed")
        QMessageBox.warning(self, "Refresh failed", f"Could not collect system information:\n\n{error}")

    def _render_items(self) -> None:
        self._clear_layout(self.content_layout)
        self.value_labels = []

        grouped: dict[str, list[InfoItem]] = {}
        for item in self.items:
            grouped.setdefault(item.category, []).append(item)

        for category, category_items in grouped.items():
            group = QGroupBox(category)
            grid = QGridLayout(group)
            grid.setColumnStretch(0, 0)
            grid.setColumnStretch(1, 1)
            grid.setHorizontalSpacing(18)
            grid.setVerticalSpacing(10)

            for row, item in enumerate(category_items):
                field = QLabel(item.label)
                field.setProperty("role", "field")
                field.setAlignment(Qt.AlignTop | Qt.AlignLeft)
                field.setMinimumWidth(230)

                value = QLabel(item.value)
                value.setProperty("role", "value")
                value.setTextInteractionFlags(Qt.TextSelectableByMouse)
                value.setWordWrap(True)
                value.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)
                self.value_labels.append(value)

                grid.addWidget(field, row, 0)
                grid.addWidget(value, row, 1)

            self.content_layout.addWidget(group)

        self.content_layout.addStretch(1)

    def _clear_layout(self, layout: QVBoxLayout) -> None:
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    def _set_buttons_enabled(self, enabled: bool) -> None:
        self.refresh_button.setEnabled(enabled)
        self.copy_button.setEnabled(enabled and bool(self.items))
        self.save_button.setEnabled(enabled and bool(self.items))

    def copy_all(self) -> None:
        if not self.items:
            return

        clipboard: QClipboard = QApplication.clipboard()
        clipboard.setText(format_report(self.items))
        self.statusBar().showMessage("Report copied to clipboard")

    def save_report(self) -> None:
        if not self.items:
            return

        report_dir = Path.home() / "Desktop"
        if not report_dir.exists():
            report_dir = Path.home()
        default_name = report_dir / "system-info-checker-report.txt"
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Save System Info Report",
            str(default_name),
            "Text Files (*.txt);;All Files (*)",
        )
        if not path:
            return

        try:
            Path(path).write_text(format_report(self.items), encoding="utf-8")
        except OSError as exc:
            QMessageBox.warning(self, "Save failed", f"Could not save the report:\n\n{exc}")
            return

        self.statusBar().showMessage(f"Report saved to {path}")

    def closeEvent(self, event: QCloseEvent) -> None:
        if self.collector_thread and self.collector_thread.isRunning():
            self.statusBar().showMessage("Waiting for refresh to finish...")
            self.collector_thread.wait(3000)
        super().closeEvent(event)


def main() -> int:
    app = QApplication(sys.argv)
    window = InfoDashboard()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
