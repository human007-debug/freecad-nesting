"""
stock_panel.py
--------------
The native app's "Stock" tab: view and edit the currently loaded inventory
(the same `panel._inventory_template`/`_inventory_path` the Nesting flow
manages), rather than only being able to hand-edit the stock file directly.
It is also the home of the inventory FILE operations now: Load, and Clear
(protected by a password confirmation). Saves/edits happen straight on the
table below (Save writes back to the loaded workbook; Save As... writes a
new one -- which is also how an inventory kept in the legacy JSON format
becomes the .xlsx everything now expects). The ribbon no longer carries any
of these.

The stock list is stored as an Excel workbook, and this table is the same
table: its columns come from `inventory.STOCK_COLUMNS`, the single
definition the .xlsx layout is also built from, so the two can't drift.

Also the home of the report's Currency picker and each stock entry's own
Scrap price/kg column -- both used only by `NestingPanel._job_financials()`/
`_build_report_html()` (shared code, so it stays reachable from there even
though the WIDGETS live here) -- Price/kg and Scrap price genuinely vary a
lot by material, so neither is a single blended setting; see
`StockSheet.scrap_price_per_kg` and `NestingPanel.currency_code`.

Refreshes automatically whenever the Nesting tab loads/clears inventory,
via `NestingPanel.inventory_changed`; the currency picker stays in sync
with `NestingPanel.currency_code` (e.g. after a job load restores a
different one) via `NestingPanel.currency_changed`.
"""

import os

from PySide6 import QtCore, QtWidgets

import inventory
from nesting_widgets import CURRENCY_SYMBOLS, DEFAULT_CURRENCY


# Password that must be entered to confirm "Clear" -- a deliberate
# fail-safe so inventory can't be wiped by a stray click.
CLEAR_PASSWORD = "4096"


def check_clear_password(password):
    """True only when `password` is the configured Clear-confirmation
    password."""
    return password == CLEAR_PASSWORD


# The workbook's own column layout (inventory.py), reused verbatim so the
# sheet a user edits in Excel and the table they edit here are one table.
# The table shows the short label and keeps the workbook's fuller heading
# as the column's tooltip.
COLUMNS = list(inventory.STOCK_COLUMN_LABELS)
COLUMN_TOOLTIPS = list(inventory.STOCK_COLUMNS)
_REMNANT_COL = len(COLUMNS) - 4
_PRICE_COL = len(COLUMNS) - 3
_DENSITY_COL = len(COLUMNS) - 2
_SCRAP_PRICE_COL = len(COLUMNS) - 1
# Numbers line up on their last digit, text doesn't: every measurement and
# price column is right-aligned so a column of them can be read down.
_NUMERIC_COLS = (2, 3, 4, 5, _PRICE_COL, _DENSITY_COL, _SCRAP_PRICE_COL)


class _StockTable(QtWidgets.QTableWidget):
    """The stock table gets the same desktop delete affordances as the parts
    table (see _PartsTable in nesting_widgets.py): the Delete/Backspace key
    and a right-click "Delete Rows" context menu. Without them the only way
    to drop a bad row is the Edit menu item -- which users never find."""

    delete_requested = QtCore.Signal()

    def __init__(self, rows, columns, parent=None):
        super().__init__(rows, columns, parent)
        self.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self._show_context_menu)

    def keyPressEvent(self, event):
        if event.key() in (QtCore.Qt.Key_Delete, QtCore.Qt.Key_Backspace):
            self.delete_requested.emit()
            event.accept()
            return
        super().keyPressEvent(event)

    def _show_context_menu(self, pos):
        row = self.rowAt(pos.y())
        if row < 0:
            return
        self.selectRow(row)
        menu = QtWidgets.QMenu(self)
        menu.addAction("Delete Rows", self.delete_requested.emit)
        menu.exec(self.viewport().mapToGlobal(pos))


class StockPanel(QtWidgets.QWidget):
    def __init__(self, panel, parent=None):
        super().__init__(parent)
        self.panel = panel

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 12)
        layout.setSpacing(10)

        title = QtWidgets.QLabel("Stock inventory")
        title.setObjectName("PageTitle")
        subtitle = QtWidgets.QLabel(
            "The sheets and offcuts this shop has on hand. Parts are matched to stock by "
            "material and thickness; the sheet size comes from whichever stock they matched."
        )
        subtitle.setObjectName("PageSubtitle")
        subtitle.setWordWrap(True)
        layout.addWidget(title)
        layout.addWidget(subtitle)

        btn_row = QtWidgets.QHBoxLayout()
        btn_row.setSpacing(8)
        load_btn = QtWidgets.QPushButton("Load Inventory...")
        load_btn.setToolTip("Load stock from an inventory workbook (.xlsx) -- or from an "
                            "inventory JSON file written by an older version.")
        load_btn.clicked.connect(self.panel._load_inventory)
        clear_btn = QtWidgets.QPushButton("Clear Inventory")
        clear_btn.setObjectName("DestructiveAction")
        clear_btn.setToolTip("Remove all loaded inventory. Requires confirmation with the "
                             "administrator password before anything is cleared.")
        clear_btn.clicked.connect(self._clear_with_confirm)
        for b in (load_btn, clear_btn):
            btn_row.addWidget(b)
        btn_row.addStretch(1)
        # Kept so a host with its own chrome (the native window's ribbon)
        # can take these over and hide this row -- see hide_header_actions().
        self._header_actions = [load_btn, clear_btn]
        currency_label = QtWidgets.QLabel("Currency:")
        self._header_actions.append(currency_label)
        btn_row.addWidget(currency_label)
        self.currency = QtWidgets.QComboBox()
        self.currency.addItems(list(CURRENCY_SYMBOLS.keys()))
        self.currency.setCurrentText(self.panel.currency_code)
        self.currency.setToolTip(
            "Display only -- every price/cost figure on this tab and in the report is a plain number "
            "underneath, no conversion. Just decides which symbol prefixes them."
        )
        self.currency.currentTextChanged.connect(self.panel.set_currency)
        self.panel.currency_changed.connect(self._sync_currency_display)
        btn_row.addWidget(self.currency)
        layout.addLayout(btn_row)

        self.table = _StockTable(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels(COLUMNS)
        for col, tip in enumerate(COLUMN_TOOLTIPS):
            item = self.table.horizontalHeaderItem(col)
            item.setToolTip(tip)
            # A heading sits over its own values: right above the numbers
            # when the column holds numbers, centered over the checkbox.
            if col in _NUMERIC_COLS:
                item.setTextAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
            elif col == _REMNANT_COL:
                item.setTextAlignment(QtCore.Qt.AlignCenter)
        header = self.table.horizontalHeader()
        # Ten columns of one table sharing the width evenly, the way the
        # spreadsheet this mirrors does. (Stretching only the LAST column,
        # as this did before, gave 40% of the table to "Scrap price/kg"
        # while truncating four headings down to "tity (blank=unlin".)
        header.setStretchLastSection(False)
        header.setSectionResizeMode(QtWidgets.QHeaderView.Stretch)
        header.setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeToContents)
        header.setSectionResizeMode(_REMNANT_COL, QtWidgets.QHeaderView.ResizeToContents)
        header.setMinimumSectionSize(76)
        header.setHighlightSections(False)
        header.setDefaultAlignment(QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter)
        self.table.setShowGrid(False)          # hairline row rules only -- see theme.py
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(32)
        self.table.delete_requested.connect(self._delete_selected_rows)
        layout.addWidget(self.table, 1)

        self.status = QtWidgets.QLabel(
            "No inventory loaded -- nesting can still run, but add stock sheets here "
            "(Load Inventory... or Add Row) once you want it to plan cost and material usage."
        )
        self.status.setObjectName("FieldHint")
        self.status.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)

        bottom_row = QtWidgets.QHBoxLayout()
        bottom_row.setSpacing(8)
        add_row_btn = QtWidgets.QPushButton("Add Row")
        add_row_btn.clicked.connect(self._add_row)
        del_row_btn = QtWidgets.QPushButton("Delete Row")
        del_row_btn.clicked.connect(self._delete_selected_rows)
        save_btn = QtWidgets.QPushButton("Save")
        save_btn.setToolTip("Writes the table above back to the loaded inventory workbook. "
                            "With nothing loaded, asks where to save it.")
        save_btn.clicked.connect(self._save)
        save_as_btn = QtWidgets.QPushButton("Save As...")
        save_as_btn.setToolTip("Writes the table above to a new inventory workbook (.xlsx) -- "
                               "also how a legacy JSON inventory is moved over to Excel.")
        save_as_btn.clicked.connect(self._save_as)
        for b in (add_row_btn, del_row_btn, save_btn, save_as_btn):
            bottom_row.addWidget(b)
        bottom_row.addStretch(1)
        bottom_row.addWidget(self.status)   # "<file> -- N entries", beside its own actions
        layout.addLayout(bottom_row)

        self.panel.inventory_changed.connect(self.refresh)
        self.refresh()

    def hide_header_actions(self):
        """Hide the Load / Clear / Currency row: the native window puts
        those on its ribbon's Stock tab (it adopts `self.currency` itself),
        and two copies of each, a few pixels apart, is one too many."""
        for widget in self._header_actions:
            widget.hide()

    # ------------------------------------------------------------ safety

    def _clear_with_confirm(self):
        """Clear Inventory with a password gate: "are you sure?" plus a
        password field. Nothing is cleared unless the password matches."""
        if not self.panel._inventory_template:
            QtWidgets.QMessageBox.information(
                self, "Clear Inventory", "No inventory is loaded -- nothing to clear."
            )
            return
        dlg = QtWidgets.QDialog(self)
        dlg.setWindowTitle("Clear Inventory")
        dlg.setModal(True)
        dlg_layout = QtWidgets.QVBoxLayout(dlg)
        prompt = QtWidgets.QLabel(
            "Are you sure you want to remove the loaded inventory?\n\n"
            "This will drop every stock entry from the job (the file on "
            "disk is left untouched). Enter the password to confirm."
        )
        prompt.setWordWrap(True)
        dlg_layout.addWidget(prompt)
        password_box = QtWidgets.QLineEdit()
        password_box.setEchoMode(QtWidgets.QLineEdit.Password)
        password_box.setPlaceholderText("Password")
        dlg_layout.addWidget(password_box)
        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel
        )
        buttons.button(QtWidgets.QDialogButtonBox.Ok).setText("Clear Inventory")
        buttons.accepted.connect(dlg.accept)
        buttons.rejected.connect(dlg.reject)
        dlg_layout.addWidget(buttons)
        password_box.setFocus()

        if dlg.exec() != QtWidgets.QDialog.Accepted:
            return  # cancelled -- inventory untouched
        if not check_clear_password(password_box.text()):
            QtWidgets.QMessageBox.warning(
                self, "Clear Inventory", "Wrong password. The inventory was NOT cleared."
            )
            return
        self.panel._clear_inventory()

    # ------------------------------------------------------------- display

    def _sync_currency_display(self, code):
        """Keeps the combobox in sync when currency_code changed from
        somewhere OTHER than this combobox (e.g. a job load) -- signals
        blocked so that sync doesn't itself re-fire set_currency()."""
        if self.currency.currentText() == code:
            return
        self.currency.blockSignals(True)
        self.currency.setCurrentText(code)
        self.currency.blockSignals(False)

    def refresh(self):
        stock = self.panel._inventory_template or []
        self.table.setRowCount(0)
        for s in stock:
            self._append_row(s)
        path = self.panel._inventory_path
        if path:
            self.status.setText(f"{os.path.basename(path)} -- {len(stock)} entries")
        else:
            self.status.setText(
                "No inventory loaded -- nesting can still run, but add stock sheets here "
                "(Load Inventory... or Add Row) once you want it to plan cost and material usage."
            )

    def _append_row(self, s):
        row = self.table.rowCount()
        self.table.insertRow(row)
        self.table.setItem(row, 0, QtWidgets.QTableWidgetItem(s.id or ""))
        self.table.setItem(row, 1, QtWidgets.QTableWidgetItem(s.material))
        self.table.setItem(row, 2, QtWidgets.QTableWidgetItem(str(s.thickness)))
        self.table.setItem(row, 3, QtWidgets.QTableWidgetItem(str(s.width)))
        self.table.setItem(row, 4, QtWidgets.QTableWidgetItem(str(s.height)))
        self.table.setItem(row, 5, QtWidgets.QTableWidgetItem("" if s.quantity is None else str(s.quantity)))
        remnant_item = QtWidgets.QTableWidgetItem()
        remnant_item.setCheckState(QtCore.Qt.Checked if s.is_remnant else QtCore.Qt.Unchecked)
        remnant_item.setTextAlignment(QtCore.Qt.AlignCenter)
        self.table.setItem(row, _REMNANT_COL, remnant_item)
        price_text = "" if s.price_per_kg is None else str(s.price_per_kg)
        self.table.setItem(row, _PRICE_COL, QtWidgets.QTableWidgetItem(price_text))
        density_text = "" if s.density_g_cm3 is None else str(s.density_g_cm3)
        self.table.setItem(row, _DENSITY_COL, QtWidgets.QTableWidgetItem(density_text))
        scrap_price_text = "" if s.scrap_price_per_kg is None else str(s.scrap_price_per_kg)
        self.table.setItem(row, _SCRAP_PRICE_COL, QtWidgets.QTableWidgetItem(scrap_price_text))
        for col in _NUMERIC_COLS:
            item = self.table.item(row, col)
            if item is not None:
                item.setTextAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)

    # --------------------------------------------------------------- edits

    def _add_row(self):
        self._append_row(inventory.StockSheet(
            material="", thickness=0.0, width=0.0, height=0.0, quantity=0, is_remnant=False, id=None,
        ))

    def _delete_selected_rows(self):
        rows = sorted({idx.row() for idx in self.table.selectedIndexes()}, reverse=True)
        for r in rows:
            self.table.removeRow(r)

    def _rows_to_stock(self):
        def text(row, col):
            item = self.table.item(row, col)
            return item.text().strip() if item else ""

        stock = []
        for row in range(self.table.rowCount()):
            qty_text = text(row, 5)
            price_text = text(row, _PRICE_COL)
            density_text = text(row, _DENSITY_COL)
            scrap_price_text = text(row, _SCRAP_PRICE_COL)
            remnant_item = self.table.item(row, _REMNANT_COL)
            stock.append(inventory.StockSheet(
                id=text(row, 0) or None,
                material=text(row, 1),
                thickness=float(text(row, 2) or 0),
                width=float(text(row, 3) or 0),
                height=float(text(row, 4) or 0),
                quantity=None if qty_text == "" else int(qty_text),
                is_remnant=bool(remnant_item and remnant_item.checkState() == QtCore.Qt.Checked),
                price_per_kg=None if price_text == "" else float(price_text),
                density_g_cm3=None if density_text == "" else float(density_text),
                scrap_price_per_kg=None if scrap_price_text == "" else float(scrap_price_text),
            ))
        return stock

    def _save(self):
        """Save over the file this inventory came from. A table with no file
        behind it yet -- built here with Add Row, or edited before anything
        was loaded -- has nowhere to write back TO, so it falls through to
        Save As rather than to an error message."""
        if not self.panel._inventory_path:
            self._save_as()
            return
        self._write(self.panel._inventory_path)

    def _save_as(self):
        current = self.panel._inventory_path
        suggested = (os.path.splitext(current)[0] + inventory.DEFAULT_INVENTORY_EXTENSION
                     if current else "inventory" + inventory.DEFAULT_INVENTORY_EXTENSION)
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "Save stock inventory as", suggested,
            "Excel workbook (*.xlsx);;Inventory JSON, legacy (*.json)",
        )
        if not path:
            return
        if not os.path.splitext(path)[1]:
            path += inventory.DEFAULT_INVENTORY_EXTENSION
        self._write(path)

    def _write(self, path):
        try:
            stock = self._rows_to_stock()
        except ValueError as e:
            QtWidgets.QMessageBox.warning(self, "Stock", f"Could not parse a row (expected a number): {e}")
            return
        try:
            inventory.save_inventory(path, stock)
        except Exception as e:
            QtWidgets.QMessageBox.warning(self, "Stock", f"Could not save inventory: {e}")
            return
        # Routed through the panel (not a bare _inventory_template assignment)
        # so a Save As actually REPOINTS the app at the new file -- the next
        # Commit writes there, and the Nesting tab's status agrees.
        self.panel._set_inventory(path, stock, f"Stock inventory saved to {path}.")
        self.status.setText(f"{os.path.basename(path)} -- {len(stock)} entries (saved).")
