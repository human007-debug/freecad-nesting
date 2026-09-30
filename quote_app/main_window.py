"""
main_window.py
--------------
AlphaQuote's window: the estimator's app, the way RADAN Radquote is
separate from RADAN's nesting -- one engine underneath, a different job on
top (docs/QUOTING_PLAN.md, Phase 4).

* Left: the quote list -- every quote in the database (quoting/db.py),
  newest first, searchable by number/title/customer, filterable by status,
  showing each quote's price at its first quantity without re-pricing.
* Right: the open quote -- its number and revision, customer, title and
  status above the same quote editor AlphaNest's Quote tab uses
  (native_app/quote_panel.py), here fed by its own parts, stock and
  currency instead of a nesting job.
* Ribbon: Home (new / duplicate / revise / delete, lines, price and
  export, status and Send to AlphaNest) and Setup (stock, rates,
  customers, currency, dark mode).

Edits save themselves: a moment after any change, on switching quotes, and
on close. A revision copies the quote into a new draft and leaves the
earlier one exactly as it was sent. Marking a quote Won records the
quantity won; Send to AlphaNest writes that many sets of its sheet parts
as a parts.json and opens AlphaNest on it, ready to nest.

App preferences (currency, dark mode, the stock and rates files) live in
QSettings under AlphaNest/AlphaQuote; quotes and customers live only in
the database.
"""

import os

from PySide6 import QtCore, QtGui, QtWidgets

import inventory
import nesting_widgets
from native_app import theme
from native_app.file_part_source import FilePartSource
from native_app.quote_panel import QuotePanel, QuoteSource
from native_app.ribbon import Ribbon
from quote_app import launch
from quoting.db import STATUSES, Customer, QuoteDB
from quoting.quote_model import nesting_parts_json, parse_breaks

SETTINGS_ORG = "AlphaNest"
SETTINGS_APP = "AlphaQuote"
AUTOSAVE_MS = 1200
LIST_COLS = ("Quote", "Customer", "Title", "Status", "Value")
STATUS_LABELS = {"draft": "Draft", "sent": "Sent", "won": "Won", "lost": "Lost"}


class CustomersDialog(QtWidgets.QDialog):
    """Add, edit and delete customers."""

    FIELDS = (("name", "Name"), ("contact", "Contact"), ("email", "Email"), ("phone", "Phone"),
              ("address", "Address"), ("notes", "Notes"))

    def __init__(self, db: QuoteDB, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Customers")
        self.resize(640, 400)
        self.db = db
        lay = QtWidgets.QHBoxLayout(self)
        left = QtWidgets.QVBoxLayout()
        self.list = QtWidgets.QListWidget()
        self.list.currentRowChanged.connect(self._show)
        left.addWidget(self.list)
        row = QtWidgets.QHBoxLayout()
        add = QtWidgets.QPushButton("New")
        add.clicked.connect(self._new)
        self.delete_btn = QtWidgets.QPushButton("Delete")
        self.delete_btn.clicked.connect(self._delete)
        row.addWidget(add)
        row.addWidget(self.delete_btn)
        left.addLayout(row)
        lay.addLayout(left, 1)
        form_box = QtWidgets.QGroupBox("Customer")
        form = QtWidgets.QFormLayout(form_box)
        self.edits = {}
        for key, label in self.FIELDS:
            w = QtWidgets.QPlainTextEdit() if key in ("address", "notes") else QtWidgets.QLineEdit()
            if isinstance(w, QtWidgets.QPlainTextEdit):
                w.setMaximumHeight(70)
            self.edits[key] = w
            form.addRow(label, w)
        save = QtWidgets.QPushButton("Save customer")
        save.clicked.connect(self._save)
        form.addRow(save)
        close = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Close)
        close.rejected.connect(self.accept)
        form.addRow(close)
        lay.addWidget(form_box, 2)
        self.customers = []
        self._reload()

    def _reload(self, select_id=None):
        self.customers = self.db.customers()
        self.list.clear()
        for c in self.customers:
            self.list.addItem(c.name)
        ids = [c.id for c in self.customers]
        self.list.setCurrentRow(ids.index(select_id) if select_id in ids else (0 if ids else -1))
        if not ids:
            self._show(-1)

    def _value(self, key):
        w = self.edits[key]
        return w.toPlainText() if isinstance(w, QtWidgets.QPlainTextEdit) else w.text()

    def _show(self, row):
        c = self.customers[row] if 0 <= row < len(self.customers) else Customer("")
        for key, w in self.edits.items():
            (w.setPlainText if isinstance(w, QtWidgets.QPlainTextEdit) else w.setText)(getattr(c, key) or "")
        self.delete_btn.setEnabled(c.id is not None)

    def _current(self):
        row = self.list.currentRow()
        return self.customers[row] if 0 <= row < len(self.customers) else None

    def _new(self):
        self.list.setCurrentRow(-1)
        self.list.clearSelection()
        self._show(-1)
        self.edits["name"].setFocus()

    def _save(self):
        values = {key: self._value(key) for key, _ in self.FIELDS}
        if not values["name"].strip():
            QtWidgets.QMessageBox.warning(self, "Customers", "A customer needs a name.")
            return
        current = self._current() if self.list.selectedItems() else None
        try:
            if current is None:
                c = self.db.add_customer(Customer(**values))
            else:
                c = Customer(id=current.id, **values)
                self.db.update_customer(c)
        except Exception as e:  # noqa: BLE001 -- e.g. a duplicate name
            QtWidgets.QMessageBox.warning(self, "Customers", f"Couldn't save: {e}")
            return
        self._reload(c.id)

    def _delete(self):
        c = self._current()
        if c is None:
            return
        if QtWidgets.QMessageBox.question(
                self, "Customers", f"Delete {c.name}? Their quotes are kept, without a customer.") \
                != QtWidgets.QMessageBox.Yes:
            return
        self.db.delete_customer(c.id)
        self._reload()


class QuoteAppWindow(QtWidgets.QMainWindow):
    def __init__(self, db_path: str):
        super().__init__()
        theme.apply_theme(QtWidgets.QApplication.instance(), False)
        mark = theme.mark_path()
        if mark:
            self.setWindowIcon(QtGui.QIcon(mark))
        self.db = QuoteDB(db_path)
        self.settings = QtCore.QSettings(SETTINGS_ORG, SETTINGS_APP)
        self.record = None
        self._dirty = False
        self._opening = False
        self.launch_alphanest = launch.launch_alphanest   # replaceable (tests)

        self.source = QuoteSource(self)
        self.part_source = FilePartSource()
        self.editor = QuotePanel(self.source, self, parts_hint="add parts from the ribbon (Add Parts)")
        self.editor.changed.connect(self._mark_dirty)
        self.editor.busy_changed.connect(lambda busy: self.price_btn.setEnabled(not busy))

        self._save_timer = QtCore.QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.timeout.connect(self.save_current)

        splitter = QtWidgets.QSplitter(QtCore.Qt.Horizontal)
        splitter.setChildrenCollapsible(False)
        splitter.addWidget(self._build_list())
        splitter.addWidget(self._build_quote_side())
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 4)
        splitter.setSizes([440, 920])
        self.setCentralWidget(splitter)

        self.ribbon = self._build_ribbon()
        bar = QtWidgets.QToolBar("Ribbon", self)
        bar.setObjectName("RibbonToolBar")
        bar.setMovable(False)
        bar.setFloatable(False)
        bar.addWidget(self.ribbon)
        bar.toggleViewAction().setVisible(False)
        self.addToolBar(QtCore.Qt.TopToolBarArea, bar)
        self.ribbon.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)

        self.db_label = QtWidgets.QLabel()
        self.statusBar().addPermanentWidget(self.db_label)
        self.db_label.setText(f"Database: {db_path}")

        self._restore_preferences()
        self.refresh_list()
        self._show_no_quote()
        first = self.db.quotes(latest_only=True)
        if first:
            self.open_quote(first[0].id)

    # ------------------------------------------------------------- layout

    def _build_list(self):
        w = QtWidgets.QWidget()
        v = QtWidgets.QVBoxLayout(w)
        v.setContentsMargins(8, 8, 4, 8)
        title = QtWidgets.QLabel("Quotes")
        f = title.font()
        f.setBold(True)
        f.setPointSizeF(f.pointSizeF() * 1.15)
        title.setFont(f)
        v.addWidget(title)
        self.search = QtWidgets.QLineEdit()
        self.search.setPlaceholderText("Search number, title or customer...")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(lambda *_: self.refresh_list())
        v.addWidget(self.search)
        row = QtWidgets.QHBoxLayout()
        self.status_filter = QtWidgets.QComboBox()
        self.status_filter.addItem("All statuses", None)
        for s in STATUSES:
            self.status_filter.addItem(STATUS_LABELS[s], s)
        self.status_filter.currentIndexChanged.connect(lambda *_: self.refresh_list())
        self.latest_only = QtWidgets.QCheckBox("Latest revisions")
        self.latest_only.setChecked(True)
        self.latest_only.toggled.connect(lambda *_: self.refresh_list())
        row.addWidget(self.status_filter, 1)
        row.addWidget(self.latest_only)
        v.addLayout(row)
        self.quote_list = QtWidgets.QTableWidget(0, len(LIST_COLS))
        self.quote_list.setHorizontalHeaderLabels(LIST_COLS)
        self.quote_list.verticalHeader().hide()
        self.quote_list.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.quote_list.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.quote_list.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        self.quote_list.itemSelectionChanged.connect(self._on_list_selection)
        v.addWidget(self.quote_list, 1)
        w.setMinimumWidth(420)
        return w

    def _build_quote_side(self):
        w = QtWidgets.QWidget()
        v = QtWidgets.QVBoxLayout(w)
        v.setContentsMargins(4, 8, 8, 0)
        head = QtWidgets.QHBoxLayout()
        self.number_label = QtWidgets.QLabel()
        f = self.number_label.font()
        f.setBold(True)
        f.setPointSizeF(f.pointSizeF() * 1.3)
        self.number_label.setFont(f)
        head.addWidget(self.number_label)
        self.status_badge = QtWidgets.QLabel()
        self.status_badge.setObjectName("QuoteStatus")
        bold = self.status_badge.font()
        bold.setBold(True)
        self.status_badge.setFont(bold)
        head.addWidget(self.status_badge)
        head.addSpacing(16)
        head.addWidget(QtWidgets.QLabel("Customer"))
        self.customer = QtWidgets.QComboBox()
        self.customer.setEditable(True)
        self.customer.setInsertPolicy(QtWidgets.QComboBox.NoInsert)
        self.customer.setMinimumWidth(220)
        self.customer.lineEdit().setPlaceholderText("Type or pick a customer")
        self.customer.lineEdit().editingFinished.connect(self._customer_edited)
        self.customer.activated.connect(lambda *_: self._customer_edited())
        head.addWidget(self.customer)
        head.addWidget(QtWidgets.QLabel("Title"))
        self.title = QtWidgets.QLineEdit()
        self.title.setPlaceholderText("What the enquiry is for")
        self.title.textEdited.connect(self._mark_dirty)
        head.addWidget(self.title, 1)
        v.addLayout(head)
        self.empty_label = QtWidgets.QLabel("No quote open -- click New Quote on the ribbon to start one.")
        self.empty_label.setAlignment(QtCore.Qt.AlignCenter)
        v.addWidget(self.empty_label)
        v.addWidget(self.editor, 1)
        return w

    def _build_ribbon(self):
        ribbon = Ribbon(self)
        home = ribbon.add_tab("Home")
        quote = home.add_group("Quote")
        new_btn = quote.add_large("new-job", "+", "New\nQuote")
        new_btn.setToolTip("Start a new quote (Ctrl+N).")
        new_btn.clicked.connect(self.new_quote)
        self.dup_btn = quote.add_small("new-job", "⎘", "Duplicate")
        self.dup_btn.setToolTip("A new quote (new number) with this one's lines and settings.")
        self.dup_btn.clicked.connect(self.duplicate_quote)
        self.revise_btn = quote.add_small("open-job", "↻", "New revision")
        self.revise_btn.setToolTip("Copy this quote into the next revision; this one stays as sent.")
        self.revise_btn.clicked.connect(self.revise_quote)
        self.delete_btn = quote.add_small("remove-part", "✕", "Delete")
        self.delete_btn.clicked.connect(self.delete_quote)

        lines = home.add_group("Lines")
        self.add_parts_btn = lines.add_large("add-parts", "+", "Add\nParts")
        self.add_parts_btn.setToolTip("Import flat parts: DXF, parts JSON, or FreeCAD/STEP files.")
        self.add_parts_btn.clicked.connect(lambda: self.add_parts())
        add_bought = lines.add_small("add-parts", "+", "Bought-in part")
        add_bought.clicked.connect(lambda: self.editor.add_line("purchased"))
        add_tube = lines.add_small("add-parts", "+", "Tube / section")
        add_tube.clicked.connect(lambda: self.editor.add_line("tube"))
        add_assy = lines.add_small("add-parts", "+", "Welded assembly")
        add_assy.clicked.connect(lambda: self.editor.add_line("assembly"))
        lines.new_column()
        remove = lines.add_small("remove-part", "−", "Remove line")
        remove.clicked.connect(self.editor.remove_selected_line)

        price = home.add_group("Price")
        self.price_btn = price.add_large("run-nesting", "▶", "Price\nQuote", primary=True)
        self.price_btn.setToolTip("Price every line at every quantity (Ctrl+P).")
        self.price_btn.clicked.connect(self.editor.price)
        export = price.add_large("report", "▦", "Export\nQuote")
        export.setToolTip("Save the quote -- summary and breakdown per quantity -- to .xlsx.")
        export.clicked.connect(self.export_quote)

        status = home.add_group("Status")
        self.status_buttons = {}
        for s, icon, glyph in (("sent", "export-dxf", "✉"), ("won", "commit-inventory", "✓"),
                               ("lost", "clear-inventory", "✕")):
            b = status.add_small(icon, glyph, f"Mark {STATUS_LABELS[s].lower()}")
            b.clicked.connect(lambda _=False, s=s: self.mark_status(s))
            self.status_buttons[s] = b
        status.new_column()
        draft = status.add_small("open-job", "✎", "Back to draft")
        draft.clicked.connect(lambda: self.mark_status("draft"))
        self.status_buttons["draft"] = draft
        self.to_nest_btn = status.add_large("commit-inventory", "➡", "Send to\nAlphaNest")
        self.to_nest_btn.setToolTip("Open this quote's sheet parts in AlphaNest, ready to nest "
                                    "(the won quantity, or one you choose).")
        self.to_nest_btn.clicked.connect(lambda: self.send_to_alphanest())

        setup = ribbon.add_tab("Setup")
        stock = setup.add_group("Stock")
        load_stock = stock.add_large("load-inventory", "⬆", "Load\nStock")
        load_stock.setToolTip("Sheet stock and prices (.xlsx, same file AlphaNest uses) -- material "
                              "is nested against it.")
        load_stock.clicked.connect(lambda: self.load_stock())
        clear_stock = stock.add_small("clear-inventory", "✕", "Clear stock")
        clear_stock.clicked.connect(self.clear_stock)
        self.stock_label = QtWidgets.QLabel()
        self.stock_label.setMinimumWidth(180)
        stock.add_widget(self.stock_label)

        rates = setup.add_group("Rates")
        load_rates = rates.add_small("load-inventory", "⬆", "Load rates...")
        load_rates.clicked.connect(lambda: self.load_rates())
        export_rates = rates.add_small("export-dxf", "⬇", "Export rates...")
        export_rates.clicked.connect(lambda: self.editor.export_rates_file())
        default_rates = rates.add_small("zoom-fit", "↺", "Starter rates")
        default_rates.setToolTip("Go back to the built-in starter rates.")
        default_rates.clicked.connect(self.use_starter_rates)

        shop = setup.add_group("Shop")
        customers = shop.add_large("parts", "☺", "Customers")
        customers.clicked.connect(self.edit_customers)
        self.currency = QtWidgets.QComboBox()
        for code, sym in nesting_widgets.CURRENCY_SYMBOLS.items():
            self.currency.addItem(f"{code} ({sym})", code)
        self.currency.currentIndexChanged.connect(
            lambda *_: self.source.set_currency(self.currency.currentData()))
        shop.add_widget(self.currency, "Currency")

        look = setup.add_group("Appearance")
        self.dark_btn = look.add_large("dark-mode", "☾", "Dark\nMode", checkable=True)
        self.dark_btn.toggled.connect(self._set_theme)

        self._quote_actions = [self.dup_btn, self.revise_btn, self.delete_btn, self.add_parts_btn,
                               add_bought, add_tube, add_assy, remove, self.price_btn, export,
                               self.to_nest_btn] + list(self.status_buttons.values())
        for key, slot in (("Ctrl+N", self.new_quote), ("Ctrl+S", self.save_current),
                          ("Ctrl+P", self.editor.price)):
            QtGui.QShortcut(QtGui.QKeySequence(key), self, activated=slot)
        return ribbon

    # --------------------------------------------------------- preferences

    def _restore_preferences(self):
        code = self.settings.value("currency", "INR")
        i = self.currency.findData(code)
        self.currency.setCurrentIndex(i if i >= 0 else 0)
        self.source.set_currency(self.currency.currentData())
        dark = self.settings.value("dark", False, type=bool)
        self.dark_btn.setChecked(dark)
        self._set_theme(dark)
        stock_path = self.settings.value("stock_path", "")
        if stock_path and os.path.exists(stock_path):
            self.load_stock(stock_path, quiet=True)
        else:
            self._refresh_stock_label()
        rates_path = self.settings.value("rates_path", "")
        if rates_path and os.path.exists(rates_path):
            self.editor.load_rates_file(rates_path)

    def _save_preferences(self):
        self.settings.setValue("currency", self.currency.currentData())
        self.settings.setValue("dark", self.dark_btn.isChecked())
        self.settings.sync()

    def _set_theme(self, dark):
        theme.apply_theme(QtWidgets.QApplication.instance(), bool(dark))
        self.ribbon.set_theme(bool(dark))

    # -------------------------------------------------------------- list

    def refresh_list(self):
        keep = self.record.id if self.record else None
        recs = self.db.quotes(search=self.search.text(), status=self.status_filter.currentData(),
                              latest_only=self.latest_only.isChecked())
        sym = nesting_widgets.CURRENCY_SYMBOLS.get(self.source.currency_code, "")
        self.quote_list.blockSignals(True)
        try:
            self.quote_list.setRowCount(len(recs))
            for r, rec in enumerate(recs):
                price = rec.price_at(0)
                sets = rec.summary[0]["sets"] if rec.summary else None
                value = f"{sym}{price:,.2f} @ {sets}" if price is not None else "—"
                if rec.status == "won" and rec.won_quantity:
                    won = next((s for s in rec.summary if s["sets"] == rec.won_quantity), None)
                    if won:
                        value = f"{sym}{won['price']:,.2f} @ {rec.won_quantity}"
                vals = (f"{rec.number} r{rec.revision}", rec.customer_name or "", rec.title,
                        STATUS_LABELS.get(rec.status, rec.status), value)
                for c, v in enumerate(vals):
                    item = QtWidgets.QTableWidgetItem(v)
                    item.setData(QtCore.Qt.UserRole, rec.id)
                    item.setToolTip(f"{rec.label} -- updated {rec.updated[:16]}")
                    self.quote_list.setItem(r, c, item)
            self.quote_list.resizeColumnsToContents()
            self.quote_list.horizontalHeader().setStretchLastSection(True)
            ids = [rec.id for rec in recs]
            if keep in ids:
                self.quote_list.selectRow(ids.index(keep))
            else:
                self.quote_list.clearSelection()
        finally:
            self.quote_list.blockSignals(False)

    def _on_list_selection(self):
        rows = {i.row() for i in self.quote_list.selectedIndexes()}
        if len(rows) != 1:
            return
        item = self.quote_list.item(rows.pop(), 0)
        if item is not None:
            self.open_quote(item.data(QtCore.Qt.UserRole))

    # ------------------------------------------------------------- quotes

    def _show_no_quote(self):
        self.record = None
        self.editor.setEnabled(False)
        self.editor.hide()
        self.empty_label.show()
        self.number_label.setText("AlphaQuote")
        self.status_badge.clear()
        for w in (self.customer, self.title):
            w.setEnabled(False)
        self.title.clear()
        self.customer.setEditText("")
        for b in getattr(self, "_quote_actions", []):
            b.setEnabled(False)
        self._refresh_title()

    def _reload_customers(self):
        text = self.customer.currentText()
        self.customer.blockSignals(True)
        self.customer.clear()
        for c in self.db.customers():
            self.customer.addItem(c.name, c.id)
        self.customer.setEditText(text)
        self.customer.blockSignals(False)

    def open_quote(self, quote_id):
        if self.record is not None and self.record.id == quote_id:
            return
        rec = self.db.quote(quote_id)
        if rec is None:
            return
        self.save_current()
        self._opening = True
        try:
            self.record = rec
            self.editor.set_state(rec.data, rec.summary)
            self.title.setText(rec.title)
            self._reload_customers()
            self.customer.setEditText(rec.customer_name or "")
            self.editor.setEnabled(True)
            self.editor.show()
            self.empty_label.hide()
            for w in (self.customer, self.title):
                w.setEnabled(True)
            for b in self._quote_actions:
                b.setEnabled(True)
            self._refresh_header()
        finally:
            self._opening = False
        self._dirty = False
        self.refresh_list()

    def _refresh_header(self):
        rec = self.record
        if rec is None:
            return
        self.number_label.setText(rec.label)
        status = STATUS_LABELS.get(rec.status, rec.status)
        if rec.status == "won" and rec.won_quantity:
            status += f" -- {rec.won_quantity} set(s)"
        self.status_badge.setText(f"  {status}  ")
        self._refresh_title()

    def _refresh_title(self):
        rec = self.record
        self.setWindowTitle(f"AlphaQuote -- {rec.label}" + (f" -- {rec.title}" if rec.title else "")
                            if rec else "AlphaQuote")

    def _mark_dirty(self, *_):
        if self._opening or self.record is None:
            return
        self._dirty = True
        self._save_timer.start(AUTOSAVE_MS)

    def save_current(self):
        """Write the open quote back to the database (no-op when unchanged)."""
        self._save_timer.stop()
        if self.record is None or not self._dirty:
            return
        rec = self.record
        rec.data = self.editor.get_state()
        if self.editor.result is not None:
            rec.summary = self.editor.priced_summary()
        rec.title = self.title.text().strip()
        self.db.save_quote(rec)
        self._dirty = False
        self._refresh_title()
        self.refresh_list()
        self.statusBar().showMessage(f"Saved {rec.label}.", 3000)

    def new_quote(self):
        self.save_current()
        rec = self.db.new_quote()
        self.refresh_list()
        self.open_quote(rec.id)
        self.title.setFocus()
        return rec

    def duplicate_quote(self):
        if self.record is None:
            return
        self.save_current()
        rec = self.db.duplicate(self.record.id)
        self.open_quote(rec.id)
        return rec

    def revise_quote(self):
        if self.record is None:
            return
        self.save_current()
        rec = self.db.revise(self.record.id)
        self.open_quote(rec.id)
        self.statusBar().showMessage(f"{rec.label} started -- rev {rec.revision - 1} is kept as it was.", 5000)
        return rec

    def delete_quote(self, confirm=True):
        rec = self.record
        if rec is None:
            return
        if confirm and QtWidgets.QMessageBox.question(
                self, "AlphaQuote", f"Delete {rec.label}? This can't be undone.") != QtWidgets.QMessageBox.Yes:
            return
        self._dirty = False
        self.db.delete_quote(rec.id)
        self._show_no_quote()
        self.refresh_list()
        remaining = self.db.quotes(latest_only=True)
        if remaining:
            self.open_quote(remaining[0].id)

    def _customer_edited(self):
        if self.record is None or self._opening:
            return
        name = self.customer.currentText().strip()
        if not name:
            new_id = None
        else:
            new_id = self.db.get_or_add_customer(name).id
        if new_id != self.record.customer_id:
            self.record.customer_id = new_id
            self.record.customer_name = name or None
            self._reload_customers()
            self._dirty = True
            self.save_current()

    def _breaks(self):
        try:
            return parse_breaks(self.editor.s_breaks.text())
        except ValueError:
            return list(self.editor.setup.breaks)

    def mark_status(self, status, won_quantity=None):
        rec = self.record
        if rec is None:
            return
        if status == "won" and won_quantity is None:
            choices = [str(b) for b in self._breaks()]
            text, ok = QtWidgets.QInputDialog.getItem(
                self, "Quote won", "Which quantity (sets) was ordered?", choices, 0, True)
            if not ok:
                return
            try:
                won_quantity = int(text)
            except ValueError:
                QtWidgets.QMessageBox.warning(self, "AlphaQuote", f"{text!r} isn't a quantity.")
                return
        self.save_current()
        rec.status = status
        rec.won_quantity = won_quantity if status == "won" else None
        self.db.save_quote(rec)
        self._refresh_header()
        self.refresh_list()

    # --------------------------------------------------------- lines/io

    def add_parts(self, paths=None):
        if self.record is None:
            return
        if paths is None:
            paths, _ = QtWidgets.QFileDialog.getOpenFileNames(
                self, "Add parts", "",
                "Supported files (*.dxf *.json *.FCStd *.step *.stp *.iges *.igs);;All files (*)")
            if not paths:
                return
        self.part_source.reset()
        extracted, logs = self.part_source.load_files(paths)
        added = self.editor.add_parts(extracted)
        problems = [l for l in logs if l.startswith(("[error]", "[warn]"))]
        if problems:
            QtWidgets.QMessageBox.warning(self, "Add parts", "\n".join(problems))
        self.statusBar().showMessage(f"Added {len(added)} part(s).", 4000)
        return added

    def export_quote(self):
        rec = self.record
        if rec is None:
            return
        default = f"{rec.number}-rev{rec.revision}.xlsx"
        path, _ = QtWidgets.QFileDialog.getSaveFileName(self, "Export quote", default, "Excel workbook (*.xlsx)")
        if path:
            self.editor.export_xlsx(path)

    def send_to_alphanest(self, sets=None):
        """Write `sets` sets of the quote's sheet parts as parts.json and open
        AlphaNest on it. Returns the file written."""
        rec = self.record
        if rec is None:
            return None
        self.save_current()
        if not any(l.part_type == "sheet" and l.outer for l in self.editor.lines):
            QtWidgets.QMessageBox.information(self, "AlphaQuote", "This quote has no sheet parts to nest.")
            return None
        if sets is None:
            sets = rec.won_quantity
        if sets is None:
            choices = [str(b) for b in self._breaks()]
            text, ok = QtWidgets.QInputDialog.getItem(self, "Send to AlphaNest", "How many sets to nest?",
                                                      choices, 0, True)
            if not ok:
                return None
            try:
                sets = int(text)
            except ValueError:
                return None
        import json
        folder = os.path.join(os.path.dirname(os.path.abspath(self.db.path)), "to-alphanest")
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, f"{rec.number}-rev{rec.revision}-{sets}sets.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(nesting_parts_json(self.editor.lines, sets), f, indent=1)
        try:
            self.launch_alphanest(path)
        except Exception as e:  # noqa: BLE001
            QtWidgets.QMessageBox.warning(self, "AlphaQuote", f"Couldn't start AlphaNest:\n{e}\n\n"
                                                              f"The parts are saved in {path}.")
            return path
        self.statusBar().showMessage(f"Opened {sets} set(s) of {rec.label} in AlphaNest.", 5000)
        return path

    def load_stock(self, path=None, quiet=False):
        if path is None:
            path, _ = QtWidgets.QFileDialog.getOpenFileName(
                self, "Load stock", "", "Stock lists (*.xlsx *.xlsm *.json);;All files (*)")
            if not path:
                return
        try:
            stock = inventory.load_inventory(path)
        except Exception as e:  # noqa: BLE001
            if not quiet:
                QtWidgets.QMessageBox.warning(self, "AlphaQuote", f"Couldn't load stock from {path}:\n{e}")
            return
        self.source.set_stock(stock, os.path.basename(path))
        self.settings.setValue("stock_path", path)
        self._refresh_stock_label()

    def clear_stock(self):
        self.source.set_stock(None)
        self.settings.setValue("stock_path", "")
        self._refresh_stock_label()

    def _refresh_stock_label(self):
        stock = self.source.stock()
        self.stock_label.setText(f"{self.source.stock_label}: {len(stock)} entries" if stock
                                 else "No stock loaded --\nstand-in sheets are used")

    def load_rates(self, path=None):
        loaded = self.editor.load_rates_file(path)
        if loaded:
            self.settings.setValue("rates_path", loaded)

    def use_starter_rates(self):
        from quoting.rates import starter_rates
        self.editor.rates = starter_rates()
        self.editor.rates_source = "Starter rates (calibrate me)"
        self.editor._refresh_rates_label()
        self.settings.setValue("rates_path", "")

    def edit_customers(self):
        CustomersDialog(self.db, self).exec()
        self._reload_customers()
        if self.record is not None:
            self.record = self.db.quote(self.record.id) or self.record
            self.customer.setEditText(self.record.customer_name or "")
        self.refresh_list()

    # ---------------------------------------------------------------- close

    def closeEvent(self, event):
        self.editor.wait()
        self.save_current()
        self._save_preferences()
        self.db.close()
        super().closeEvent(event)
