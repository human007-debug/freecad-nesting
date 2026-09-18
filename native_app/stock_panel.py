"""
stock_panel.py
--------------
The native app's "Stock" tab: view and edit the currently loaded inventory
(the same `panel._inventory_template`/`_inventory_path` the Nesting flow
manages), rather than only being able to hand-edit the JSON file directly.
It is also the home of the inventory FILE operations now: Load, and Clear
(protected by a password confirmation). Saves/edits happen straight on the
table below (Save writes back to the loaded JSON file). The ribbon no
longer carries any of these.

Refreshes automatically whenever the Nesting tab loads/clears inventory,
via `NestingPanel.inventory_changed`.
"""

import os

from PySide6 import QtCore, QtWidgets

import inventory


# Password that must be entered to confirm "Clear" -- a deliberate
# fail-safe so inventory can't be wiped by a stray click.
CLEAR_PASSWORD = "4096"


def check_clear_password(password):
    """True only when `password` is the configured Clear-confirmation
    password."""
    return password == CLEAR_PASSWORD


COLUMNS = ["ID", "Material", "Thickness (mm)", "Width (mm)", "Height (mm)", "Quantity (blank=unlimited)",
           "Remnant", "Price/sheet (blank=unpriced)"]
_REMNANT_COL = len(COLUMNS) - 2
_PRICE_COL = len(COLUMNS) - 1


class StockPanel(QtWidgets.QWidget):
    def __init__(self, panel, parent=None):
        super().__init__(parent)
        self.panel = panel

        layout = QtWidgets.QVBoxLayout(self)

        btn_row = QtWidgets.QHBoxLayout()
        load_btn = QtWidgets.QPushButton("Load Inventory...")
        load_btn.setToolTip("Load stock from an inventory JSON file.")
        load_btn.clicked.connect(self.panel._load_inventory)
        clear_btn = QtWidgets.QPushButton("Clear Inventory")
        clear_btn.setObjectName("DestructiveAction")
        clear_btn.setToolTip("Remove all loaded inventory. Requires confirmation with the "
                             "administrator password before anything is cleared.")
        clear_btn.clicked.connect(self._clear_with_confirm)
        for b in (load_btn, clear_btn):
            btn_row.addWidget(b)
        btn_row.addStretch(1)
        layout.addLayout(btn_row)

        self.table = QtWidgets.QTableWidget(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels(COLUMNS)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.verticalHeader().setVisible(False)
        layout.addWidget(self.table)

        self.status = QtWidgets.QLabel("No inventory loaded.")
        layout.addWidget(self.status)

        bottom_row = QtWidgets.QHBoxLayout()
        add_row_btn = QtWidgets.QPushButton("Add Row")
        add_row_btn.clicked.connect(self._add_row)
        del_row_btn = QtWidgets.QPushButton("Delete Row")
        del_row_btn.clicked.connect(self._delete_selected_rows)
        save_btn = QtWidgets.QPushButton("Save")
        save_btn.setToolTip("Writes the table below back to the loaded inventory JSON file.")
        save_btn.clicked.connect(self._save)
        for b in (add_row_btn, del_row_btn, save_btn):
            bottom_row.addWidget(b)
        bottom_row.addStretch(1)
        layout.addLayout(bottom_row)

        self.panel.inventory_changed.connect(self.refresh)
        self.refresh()

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
            "This will drop every stock entry from the job (the JSON file on "
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

    def refresh(self):
        stock = self.panel._inventory_template or []
        self.table.setRowCount(0)
        for s in stock:
            self._append_row(s)
        path = self.panel._inventory_path
        self.status.setText(
            f"{os.path.basename(path)} -- {len(stock)} entries" if path else "No inventory loaded."
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
        self.table.setItem(row, _REMNANT_COL, remnant_item)
        price_text = "" if s.price_per_sheet is None else str(s.price_per_sheet)
        self.table.setItem(row, _PRICE_COL, QtWidgets.QTableWidgetItem(price_text))

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
            remnant_item = self.table.item(row, _REMNANT_COL)
            stock.append(inventory.StockSheet(
                id=text(row, 0) or None,
                material=text(row, 1),
                thickness=float(text(row, 2) or 0),
                width=float(text(row, 3) or 0),
                height=float(text(row, 4) or 0),
                quantity=None if qty_text == "" else int(qty_text),
                is_remnant=bool(remnant_item and remnant_item.checkState() == QtCore.Qt.Checked),
                price_per_sheet=None if price_text == "" else float(price_text),
            ))
        return stock

    def _save(self):
        if not self.panel._inventory_path:
            QtWidgets.QMessageBox.warning(self, "Stock", "No inventory file loaded -- use Load Inventory first.")
            return
        try:
            stock = self._rows_to_stock()
        except ValueError as e:
            QtWidgets.QMessageBox.warning(self, "Stock", f"Could not parse a row (expected a number): {e}")
            return
        inventory.save_inventory(self.panel._inventory_path, stock)
        self.panel._inventory_template = stock
        self.status.setText(f"{os.path.basename(self.panel._inventory_path)} -- {len(stock)} entries (saved).")
        self.panel._log(f"Stock inventory saved to {self.panel._inventory_path}.")
