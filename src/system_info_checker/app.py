"""PySide6 desktop app for System Info Checker."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import sys

from PySide6.QtCore import QThread, Qt, Signal
from PySide6.QtGui import QAction, QClipboard, QCloseEvent, QFont
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLayout,
    QLineEdit,
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


UNAVAILABLE_TEXT = "Unavailable"


@dataclass
class RowView:
    """Rendered state for one collected item."""

    item: InfoItem
    widget: QFrame
    haystack: str


@dataclass
class CategoryView:
    """Rendered state for one category section."""

    widget: QWidget
    rows: list[RowView]


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
        self.category_views: dict[str, CategoryView] = {}
        self.collector_thread: CollectorThread | None = None

        self.setWindowTitle(f"{__app_name__} {__version__}")
        self.resize(1220, 820)
        self.setMinimumSize(940, 620)

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
        root.setObjectName("AppRoot")
        root_layout = QVBoxLayout(root)
        root_layout.setContentsMargins(28, 24, 28, 22)
        root_layout.setSpacing(18)

        top_bar = QHBoxLayout()
        top_bar.setSpacing(14)

        title_stack = QVBoxLayout()
        title_stack.setSpacing(4)
        self.window_title = QLabel(__app_name__)
        self.window_title.setObjectName("PageTitle")
        self.window_subtitle = QLabel(f"Version {__version__} - read-only device, network, firmware, and runtime details")
        self.window_subtitle.setObjectName("PageSubtitle")
        self.window_subtitle.setWordWrap(True)
        title_stack.addWidget(self.window_title)
        title_stack.addWidget(self.window_subtitle)
        top_bar.addLayout(title_stack, stretch=1)

        self.scan_state = QLabel("Starting scan")
        self.scan_state.setObjectName("StatePill")
        self.scan_state.setAlignment(Qt.AlignCenter)
        self.scan_state.setMinimumHeight(38)
        top_bar.addWidget(self.scan_state)

        self.refresh_button = self._make_button("Refresh", "primary")
        self.refresh_button.clicked.connect(self.refresh)
        self.copy_button = self._make_button("Copy All", "secondary")
        self.copy_button.clicked.connect(self.copy_all)
        self.save_button = self._make_button("Save", "secondary")
        self.save_button.clicked.connect(self.save_report)
        top_bar.addWidget(self.refresh_button)
        top_bar.addWidget(self.copy_button)
        top_bar.addWidget(self.save_button)
        root_layout.addLayout(top_bar)

        self.summary_grid = QGridLayout()
        self.summary_grid.setContentsMargins(0, 0, 0, 0)
        self.summary_grid.setHorizontalSpacing(12)
        self.summary_grid.setVerticalSpacing(12)
        self.summary_labels: dict[str, QLabel] = {}
        for index, (key, label) in enumerate(
            (
                ("os", "Operating System"),
                ("cpu", "Processor"),
                ("ram", "Memory"),
                ("network", "Network"),
            )
        ):
            card = self._make_summary_card(key, label)
            self.summary_grid.addWidget(card, 0, index)
        root_layout.addLayout(self.summary_grid)

        controls = QHBoxLayout()
        controls.setSpacing(12)
        self.search_input = QLineEdit()
        self.search_input.setObjectName("SearchInput")
        self.search_input.setPlaceholderText("Search system details")
        self.search_input.textChanged.connect(self._filter_items)
        controls.addWidget(self.search_input, stretch=1)

        self.clear_search_button = self._make_button("Clear", "quiet")
        self.clear_search_button.clicked.connect(self.search_input.clear)
        controls.addWidget(self.clear_search_button)
        root_layout.addLayout(controls)

        self.scroll_area = QScrollArea()
        self.scroll_area.setObjectName("ContentScrollArea")
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setFrameShape(QFrame.NoFrame)

        self.content = QWidget()
        self.content.setObjectName("ContentCanvas")
        self.content_layout = QVBoxLayout(self.content)
        self.content_layout.setContentsMargins(0, 0, 14, 0)
        self.content_layout.setSpacing(18)
        self.scroll_area.setWidget(self.content)
        root_layout.addWidget(self.scroll_area, stretch=1)

        self.setCentralWidget(root)

        status = QStatusBar()
        status.showMessage("Ready")
        self.setStatusBar(status)

    def _make_button(self, text: str, role: str) -> QPushButton:
        button = QPushButton(text)
        button.setProperty("role", role)
        button.setCursor(Qt.PointingHandCursor)
        button.setMinimumHeight(38)
        return button

    def _make_summary_card(self, key: str, label: str) -> QFrame:
        card = QFrame()
        card.setProperty("role", "summaryCard")
        card.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(16, 14, 16, 15)
        layout.setSpacing(8)

        title = QLabel(label)
        title.setProperty("role", "summaryTitle")
        value = QLabel("--")
        value.setProperty("role", "summaryValue")
        value.setWordWrap(True)
        value.setMinimumHeight(44)
        value.setAlignment(Qt.AlignTop | Qt.AlignLeft)

        layout.addWidget(title)
        layout.addWidget(value)
        self.summary_labels[key] = value
        return card

    def _apply_theme(self) -> None:
        app = QApplication.instance()
        if app:
            app.setStyle("Fusion")
            app.setFont(QFont("Segoe UI", 10))

        self.setStyleSheet(
            """
            QMainWindow, #AppRoot, #ContentCanvas {
                background: #101214;
                color: #f4f1ea;
            }
            #StatePill {
                background: #263125;
                border: 1px solid #496848;
                border-radius: 8px;
                color: #b9e6b4;
                font-size: 12px;
                font-weight: 700;
                padding: 7px 10px;
            }
            #PageTitle {
                color: #fff8e8;
                font-size: 30px;
                font-weight: 850;
            }
            #PageSubtitle {
                color: #aeb4bd;
                font-size: 13px;
            }
            QScrollArea, #ContentScrollArea {
                background: transparent;
            }
            QScrollBar:vertical {
                background: transparent;
                border: none;
                width: 12px;
                margin: 2px 0 2px 8px;
            }
            QScrollBar::handle:vertical {
                background: #4a4f55;
                border-radius: 5px;
                min-height: 52px;
            }
            QScrollBar::add-line:vertical,
            QScrollBar::sub-line:vertical {
                height: 0;
            }
            QFrame[role="summaryCard"] {
                background: #1d2024;
                border: 1px solid #33383f;
                border-radius: 8px;
            }
            QLabel[role="summaryTitle"] {
                background: transparent;
                color: #d8b45a;
                font-size: 11px;
                font-weight: 800;
            }
            QLabel[role="summaryValue"] {
                background: transparent;
                color: #f5f1e7;
                font-size: 14px;
                font-weight: 700;
            }
            #SearchInput {
                background: #1b1e22;
                border: 1px solid #3a4048;
                border-radius: 8px;
                color: #f4f1ea;
                padding: 9px 12px;
                selection-background-color: #5aa7a7;
            }
            #SearchInput:focus {
                border-color: #5aa7a7;
            }
            #SearchInput::placeholder {
                color: #777f88;
            }
            QPushButton {
                border-radius: 8px;
                padding: 8px 14px;
                font-weight: 800;
            }
            QPushButton[role="primary"] {
                background: #2f7d62;
                border: 1px solid #43b883;
                color: #ffffff;
            }
            QPushButton[role="primary"]:hover {
                background: #368f70;
            }
            QPushButton[role="secondary"] {
                background: #252a30;
                border: 1px solid #454b55;
                color: #f4f1ea;
            }
            QPushButton[role="secondary"]:hover {
                background: #303640;
                border-color: #5aa7a7;
            }
            QPushButton[role="quiet"] {
                background: transparent;
                border: 1px solid #383d44;
                color: #bdc3cb;
            }
            QPushButton[role="quiet"]:hover {
                background: #22262b;
                color: #f4f1ea;
            }
            QPushButton[role="rowCopy"] {
                background: #24282d;
                border: 1px solid #414851;
                color: #cfd5dd;
                padding: 6px 10px;
                min-height: 28px;
            }
            QPushButton[role="rowCopy"]:hover {
                background: #30363d;
                border-color: #d8b45a;
                color: #fff8e8;
            }
            QPushButton:disabled {
                background: #25282d;
                border-color: #30343a;
                color: #727982;
            }
            QLabel[role="categoryTitle"] {
                background: transparent;
                color: #fff8e8;
                font-size: 18px;
                font-weight: 850;
            }
            QWidget[role="section"] {
                background: transparent;
            }
            QFrame[role="infoRow"] {
                background: #181b1f;
                border: 1px solid #2d3239;
                border-radius: 8px;
            }
            QFrame[role="infoRow"][state="unavailable"] {
                border-color: #52323a;
                background: #1f1b1f;
            }
            QLabel[role="field"] {
                background: transparent;
                color: #aeb5bf;
                font-size: 12px;
                font-weight: 800;
            }
            QLabel[role="value"] {
                background: transparent;
                color: #f6f2e9;
                font-size: 13px;
                selection-background-color: #5aa7a7;
            }
            QLabel[role="value"][state="unavailable"] {
                color: #e05c75;
                font-weight: 800;
            }
            QStatusBar {
                background: #181a1d;
                color: #aeb4bd;
                border-top: 1px solid #2b2d31;
            }
            """
        )

    def refresh(self) -> None:
        if self.collector_thread and self.collector_thread.isRunning():
            return

        self.statusBar().showMessage("Refreshing system information...")
        self.scan_state.setText("Scanning")
        self._set_buttons_enabled(False)

        self.collector_thread = CollectorThread()
        self.collector_thread.finished_collecting.connect(self._refresh_complete)
        self.collector_thread.failed.connect(self._refresh_failed)
        self.collector_thread.start()

    def _refresh_complete(self, items: list[InfoItem]) -> None:
        self.items = items
        self._render_items()
        self._update_overview()
        self._filter_items(self.search_input.text())
        self._set_buttons_enabled(True)
        self.scan_state.setText("Scan complete")
        self.statusBar().showMessage("System information refreshed")

    def _refresh_failed(self, error: str) -> None:
        self._set_buttons_enabled(True)
        self.scan_state.setText("Scan failed")
        self.statusBar().showMessage("Refresh failed")
        QMessageBox.warning(self, "Refresh failed", f"Could not collect system information:\n\n{error}")

    def _render_items(self) -> None:
        self._clear_layout(self.content_layout)
        self.category_views = {}

        grouped: dict[str, list[InfoItem]] = {}
        for item in self.items:
            grouped.setdefault(item.category, []).append(item)

        for category, category_items in grouped.items():
            section = QWidget()
            section.setProperty("role", "section")
            section_layout = QVBoxLayout(section)
            section_layout.setContentsMargins(0, 0, 0, 0)
            section_layout.setSpacing(10)

            heading = QHBoxLayout()
            heading.setSpacing(10)
            title = QLabel(category)
            title.setProperty("role", "categoryTitle")
            title.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
            heading.addWidget(title, stretch=1)
            section_layout.addLayout(heading)

            rows = [self._make_info_row(item) for item in category_items]
            for row_view in rows:
                section_layout.addWidget(row_view.widget)

            self.content_layout.addWidget(section)

            self.category_views[category] = CategoryView(section, rows)

        self.content_layout.addStretch(1)

    def _make_info_row(self, item: InfoItem) -> RowView:
        row = QFrame()
        row.setProperty("role", "infoRow")
        if item.value == UNAVAILABLE_TEXT:
            row.setProperty("state", "unavailable")
        layout = QHBoxLayout(row)
        layout.setContentsMargins(15, 12, 12, 12)
        layout.setSpacing(14)

        text_stack = QVBoxLayout()
        text_stack.setSpacing(5)

        field = QLabel(item.label)
        field.setProperty("role", "field")
        field.setWordWrap(True)
        field.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)

        value = QLabel(item.value)
        value.setProperty("role", "value")
        if item.value == UNAVAILABLE_TEXT:
            value.setProperty("state", "unavailable")
        value.setTextInteractionFlags(Qt.TextSelectableByMouse)
        value.setWordWrap(True)
        value.setMinimumWidth(0)
        value.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)

        text_stack.addWidget(field)
        text_stack.addWidget(value)
        layout.addLayout(text_stack, stretch=1)

        copy_button = QPushButton("Copy")
        copy_button.setProperty("role", "rowCopy")
        copy_button.setCursor(Qt.PointingHandCursor)
        copy_button.clicked.connect(lambda _checked=False, copied_item=item: self.copy_item(copied_item))
        layout.addWidget(copy_button, alignment=Qt.AlignTop)

        haystack = f"{item.category} {item.label} {item.value}".casefold()
        return RowView(item=item, widget=row, haystack=haystack)

    def _update_overview(self) -> None:
        self.summary_labels["os"].setText(self._value_for("OS version/build"))
        self.summary_labels["cpu"].setText(self._shorten(self._value_for("CPU name"), 84))
        self.summary_labels["ram"].setText(self._value_for("RAM amount"))
        self.summary_labels["network"].setText(self._network_summary())

    def _value_for(self, label: str) -> str:
        for item in self.items:
            if item.label == label:
                return item.value
        return "--"

    def _network_summary(self) -> str:
        local_ip = self._value_for("Local IP address")
        public_ip = self._value_for("Public IP address")
        if local_ip != UNAVAILABLE_TEXT:
            return f"Local {local_ip}"
        if public_ip != UNAVAILABLE_TEXT:
            return f"Public {public_ip}"
        return UNAVAILABLE_TEXT

    def _shorten(self, value: str, limit: int) -> str:
        if len(value) <= limit:
            return value
        return f"{value[: limit - 3].rstrip()}..."

    def _filter_items(self, query: str) -> None:
        normalized = query.casefold().strip()

        for category_view in self.category_views.values():
            visible_rows = 0
            for row_view in category_view.rows:
                is_visible = not normalized or normalized in row_view.haystack
                row_view.widget.setVisible(is_visible)
                if is_visible:
                    visible_rows += 1

            category_view.widget.setVisible(visible_rows > 0)

    def _clear_layout(self, layout: QLayout) -> None:
        while layout.count():
            item = layout.takeAt(0)
            child_layout = item.layout()
            widget = item.widget()
            if child_layout is not None:
                self._clear_layout(child_layout)
            if widget is not None:
                widget.deleteLater()

    def _set_buttons_enabled(self, enabled: bool) -> None:
        self.refresh_button.setEnabled(enabled)
        self.copy_button.setEnabled(enabled and bool(self.items))
        self.save_button.setEnabled(enabled and bool(self.items))
        self.search_input.setEnabled(enabled and bool(self.items))
        self.clear_search_button.setEnabled(enabled and bool(self.items))

    def copy_item(self, item: InfoItem) -> None:
        clipboard: QClipboard = QApplication.clipboard()
        clipboard.setText(f"{item.label}: {item.value}")
        self.statusBar().showMessage(f"Copied {item.label}")

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
