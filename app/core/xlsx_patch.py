"""Edit individual cells of an .xlsx / .xlsm file directly in its XML.

Why not just re-save with openpyxl? Re-saving rebuilds the whole file: it drops the cached
values of every formula, can silently lose features openpyxl does not support (images,
slicers, some conditional formatting, data-validation extensions), and has to load the
very large HC_Teamlease sheet. Patching the XML changes only the cells we write. Every
other zip entry, including the VBA project and all other sheets, is copied unchanged.

A new sheet can be added (TO CONSIDER); that also touches workbook.xml, its rels,
[Content_Types].xml and styles.xml (for the bold / date / border cell styles).

Strings are written as inline strings (t="inlineStr"), so sharedStrings.xml is never touched.
Formulas are written without a cached value, and the workbook is flagged to recalculate
fully when it is opened in Excel.
"""

import copy
import posixpath
import re
import zipfile
from dataclasses import dataclass
from io import BytesIO

from lxml import etree

NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PKG_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
CT_NS = "http://schemas.openxmlformats.org/package/2006/content-types"
CALC_CHAIN_TYPE = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/calcChain"
WORKSHEET_TYPE = f"{REL_NS}/worksheet"
WORKSHEET_CONTENT_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"
STYLES_PATH = "xl/styles.xml"

EMPTY_SHEET = (
    f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<worksheet xmlns="{NS}" xmlns:r="{REL_NS}">'
    '<sheetViews><sheetView workbookViewId="0"/></sheetViews><sheetFormatPr defaultRowHeight="15"/>'
    "<sheetData/></worksheet>"
).encode()

_REF = re.compile(r"^([A-Z]+)(\d+)$")
_PARSER = etree.XMLParser(huge_tree=True, remove_blank_text=False, resolve_entities=False)

# Elements that may come after <calcPr> in <workbook>, in schema order (calcPr must go before them).
_AFTER_CALC_PR = ["oleSize", "customWorkbookViews", "pivotCaches", "smartTagPr", "smartTagTypes",
                  "webPublishing", "fileRecoveryPr", "webPublishObjects", "extLst"]


class XlsxError(Exception):
    pass


def q(tag: str) -> str:
    return f"{{{NS}}}{tag}"


def column_index(letters: str) -> int:
    index = 0
    for char in letters:
        index = index * 26 + (ord(char) - 64)
    return index


def split_ref(ref: str) -> tuple[str, int]:
    match = _REF.match(ref)
    if not match:
        raise XlsxError(f"Invalid cell reference {ref!r}")
    return match.group(1), int(match.group(2))


@dataclass
class CellState:
    kind: str  # "empty" | "value" | "formula"
    formula: str | None = None
    shared_master: bool = False


class SheetEditor:
    """Cell-level editing of one worksheet XML."""

    def __init__(self, xml: bytes) -> None:
        self.root = etree.fromstring(xml, _PARSER)
        self.sheet_data = self.root.find(q("sheetData"))
        if self.sheet_data is None:
            raise XlsxError("Worksheet has no sheetData")
        self._rows = {int(row.get("r")): row for row in self.sheet_data.iter(q("row")) if row.get("r")}
        self._cells: dict[int, dict[str, etree._Element]] = {}
        self.changed = False

    # --- lookup ---

    def _row(self, number: int, create: bool) -> etree._Element | None:
        row = self._rows.get(number)
        if row is None and create:
            row = etree.Element(q("row"), r=str(number))
            following = [r for n, r in self._rows.items() if n > number]
            if following:
                min(following, key=lambda r: int(r.get("r"))).addprevious(row)
            else:
                self.sheet_data.append(row)
            self._rows[number] = row
        return row

    def _row_cells(self, number: int, row: etree._Element) -> dict[str, etree._Element]:
        if number not in self._cells:
            cells = {}
            for cell in row.iter(q("c")):
                if cell.get("r") is None:
                    raise XlsxError(f"Row {number} has a cell without a reference; cannot edit safely")
                cells[cell.get("r")] = cell
            self._cells[number] = cells
        return self._cells[number]

    def _cell(self, ref: str, create: bool) -> etree._Element | None:
        letters, number = split_ref(ref)
        row = self._row(number, create)
        if row is None:
            return None
        cells = self._row_cells(number, row)
        cell = cells.get(ref)
        if cell is None and create:
            cell = etree.Element(q("c"), r=ref)
            index = column_index(letters)
            following = [c for r, c in cells.items() if column_index(split_ref(r)[0]) > index]
            if following:
                min(following, key=lambda c: column_index(split_ref(c.get("r"))[0])).addprevious(cell)
            else:
                row.append(cell)
            cells[ref] = cell
            row.attrib.pop("spans", None)  # optional hint; drop it rather than keep a wrong one
        return cell

    def state(self, ref: str) -> CellState:
        cell = self._cell(ref, create=False)
        if cell is None:
            return CellState("empty")
        formula = cell.find(q("f"))
        if formula is not None:
            return CellState("formula", formula.text, shared_master=formula.get("t") == "shared" and formula.get("ref") is not None)
        value, inline = cell.find(q("v")), cell.find(q("is"))
        if (value is not None and (value.text or "") != "") or (inline is not None and "".join(inline.itertext()) != ""):
            return CellState("value")
        return CellState("empty")

    def raw_value(self, ref: str) -> tuple[str | None, str | None]:
        """(cell type attribute, text of <v> or of the inline string), without resolving shared strings."""
        cell = self._cell(ref, create=False)
        if cell is None:
            return None, None
        inline = cell.find(q("is"))
        if inline is not None:
            return "inlineStr", "".join(t.text or "" for t in inline.iter(q("t")))
        value = cell.find(q("v"))
        return cell.get("t"), value.text if value is not None else None

    # --- writes ---

    def _reset(self, cell: etree._Element) -> None:
        for child in list(cell):
            if child.tag in (q("f"), q("v"), q("is")):
                cell.remove(child)
        cell.attrib.pop("t", None)
        self.changed = True

    def set_text(self, ref: str, text: str) -> None:
        cell = self._cell(ref, create=True)
        self._reset(cell)
        cell.set("t", "inlineStr")
        inline = etree.Element(q("is"))
        etree.SubElement(inline, q("t")).text = text
        cell.insert(0, inline)

    def set_formula(self, ref: str, formula: str) -> None:
        cell = self._cell(ref, create=True)
        self._reset(cell)
        element = etree.Element(q("f"))
        element.text = formula[1:] if formula.startswith("=") else formula
        cell.insert(0, element)

    def set_number(self, ref: str, value: int | float) -> None:
        cell = self._cell(ref, create=True)
        self._reset(cell)
        number = int(value) if isinstance(value, float) and value.is_integer() else value
        etree.SubElement(cell, q("v")).text = repr(number) if isinstance(number, float) else str(number)

    def set_error(self, ref: str, text: str) -> None:
        """An error value such as #N/A, as a failed formula would show it."""
        cell = self._cell(ref, create=True)
        self._reset(cell)
        cell.set("t", "e")
        etree.SubElement(cell, q("v")).text = text

    def set_style(self, ref: str, style: int) -> None:
        """Apply a cellXfs index (from XlsxPatcher.add_style); creates the cell if needed."""
        self._cell(ref, create=True).set("s", str(style))
        self.changed = True

    def set_column_widths(self, widths: dict[str, float]) -> None:
        """Replace the <cols> element with one width per column letter."""
        for old in self.root.findall(q("cols")):
            self.root.remove(old)
        if not widths:
            return
        cols = etree.Element(q("cols"))
        for letters, width in sorted(widths.items(), key=lambda item: column_index(item[0])):
            index = str(column_index(letters))
            etree.SubElement(cols, q("col"), min=index, max=index, width=str(width), customWidth="1")
        self.sheet_data.addprevious(cols)
        self.changed = True

    def clear(self, ref: str) -> None:
        """Remove the value or formula but keep the cell and its style."""
        cell = self._cell(ref, create=False)
        if cell is not None:
            self._reset(cell)

    def to_bytes(self) -> bytes:
        return etree.tostring(self.root, xml_declaration=True, encoding="UTF-8", standalone=True)


class XlsxPatcher:
    """Open a workbook, edit some sheets, and write it back with everything else unchanged."""

    def __init__(self, data: bytes) -> None:
        try:
            self._zip = zipfile.ZipFile(BytesIO(data))
        except zipfile.BadZipFile as exc:
            raise XlsxError("Not an Excel workbook") from exc
        self._replaced: dict[str, bytes] = {}
        self._removed: set[str] = set()
        self._sheets: dict[str, SheetEditor] = {}
        self._shared_strings: list[str] | None = None
        self._sheet_paths = self._find_sheet_paths()
        self._styles: etree._Element | None = None
        self._style_ids: dict[object, int] = {}  # (bold, number_format, border) -> xf index, plus font/border ids

    def _read(self, name: str) -> bytes:
        return self._replaced.get(name) or self._zip.read(name)

    def _xml(self, name: str) -> etree._Element:
        return etree.fromstring(self._read(name), _PARSER)

    def _put(self, name: str, root: etree._Element) -> None:
        self._replaced[name] = etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)

    def _find_sheet_paths(self) -> dict[str, str]:
        workbook = etree.fromstring(self._read("xl/workbook.xml"), _PARSER)
        rels = etree.fromstring(self._read("xl/_rels/workbook.xml.rels"), _PARSER)
        targets = {rel.get("Id"): rel.get("Target") for rel in rels}
        paths = {}
        for sheet in workbook.iter(q("sheet")):
            target = targets.get(sheet.get(f"{{{REL_NS}}}id"), "")
            path = target.lstrip("/") if target.startswith("/") else posixpath.normpath(posixpath.join("xl", target))
            paths[sheet.get("name").strip().upper()] = path
        return paths

    def sheet(self, name: str) -> SheetEditor:
        key = name.strip().upper()
        if key not in self._sheets:
            if key not in self._sheet_paths:
                raise XlsxError(f"The workbook has no '{name}' sheet")
            self._sheets[key] = SheetEditor(self._read(self._sheet_paths[key]))
        return self._sheets[key]

    def has_sheet(self, name: str) -> bool:
        return name.strip().upper() in self._sheet_paths

    def add_sheet(self, name: str, position: int = 0) -> SheetEditor:
        """Add an empty worksheet at `position` in the tab order and return its editor.

        The sheet that was active stays active, so no other sheet's XML has to change.
        """
        key = name.strip().upper()
        if key in self._sheet_paths:
            raise XlsxError(f"The workbook already has a '{name}' sheet")
        taken = set(self._zip.namelist()) | set(self._replaced)
        number = 1
        while f"xl/worksheets/sheet{number}.xml" in taken:
            number += 1
        path = f"xl/worksheets/sheet{number}.xml"

        rels = self._xml("xl/_rels/workbook.xml.rels")
        used = [int(rel.get("Id")[3:]) for rel in rels if re.fullmatch(r"rId\d+", rel.get("Id") or "")]
        rel_id = f"rId{max(used, default=0) + 1}"
        etree.SubElement(rels, f"{{{PKG_REL_NS}}}Relationship", Id=rel_id, Type=WORKSHEET_TYPE,
                         Target=f"worksheets/sheet{number}.xml")
        self._put("xl/_rels/workbook.xml.rels", rels)

        workbook = self._xml("xl/workbook.xml")
        sheets = workbook.find(q("sheets"))
        sheet_id = max((int(s.get("sheetId")) for s in sheets), default=0) + 1
        element = etree.Element(q("sheet"), name=name, sheetId=str(sheet_id))
        element.set(f"{{{REL_NS}}}id", rel_id)
        sheets.insert(position, element)
        self._shift_sheet_indexes(workbook, position, +1)
        self._put("xl/workbook.xml", workbook)

        types = self._xml("[Content_Types].xml")
        etree.SubElement(types, f"{{{CT_NS}}}Override", PartName=f"/{path}", ContentType=WORKSHEET_CONTENT_TYPE)
        self._put("[Content_Types].xml", types)

        self._sheet_paths[key] = path
        editor = SheetEditor(EMPTY_SHEET)
        editor.changed = True
        self._sheets[key] = editor
        return editor

    def remove_sheet(self, name: str) -> None:
        """Delete a worksheet and its relationships part. Used by the side-by-side check only."""
        key = name.strip().upper()
        if key not in self._sheet_paths:
            raise XlsxError(f"The workbook has no '{name}' sheet")
        path = self._sheet_paths.pop(key)
        self._sheets.pop(key, None)

        workbook = self._xml("xl/workbook.xml")
        sheets = workbook.find(q("sheets"))
        element = next(s for s in sheets if s.get("name").strip().upper() == key)
        index, rel_id = list(sheets).index(element), element.get(f"{{{REL_NS}}}id")
        sheets.remove(element)
        for defined in list(workbook.iter(q("definedName"))):
            if defined.get("localSheetId") == str(index):
                defined.getparent().remove(defined)
        self._shift_sheet_indexes(workbook, index + 1, -1)
        self._put("xl/workbook.xml", workbook)

        rels = self._xml("xl/_rels/workbook.xml.rels")
        for rel in list(rels):
            if rel.get("Id") == rel_id:
                rels.remove(rel)
        self._put("xl/_rels/workbook.xml.rels", rels)

        types = self._xml("[Content_Types].xml")
        for override in list(types):
            if override.get("PartName") == f"/{path}":
                types.remove(override)
        self._put("[Content_Types].xml", types)

        self._removed.add(path)
        self._removed.add(posixpath.join(posixpath.dirname(path), "_rels", posixpath.basename(path) + ".rels"))
        self.drop_calc_chain()

    @staticmethod
    def _shift_sheet_indexes(workbook: etree._Element, start: int, delta: int) -> None:
        """Sheet-position references (local defined names, active and first tab) at or after `start` move by `delta`."""
        for defined in workbook.iter(q("definedName")):
            local = defined.get("localSheetId")
            if local is not None and int(local) >= start:
                defined.set("localSheetId", str(int(local) + delta))
        for view in workbook.iter(q("workbookView")):
            for attribute in ("activeTab", "firstSheet"):
                value = int(view.get(attribute, "0"))
                if value >= start:
                    view.set(attribute, str(max(value + delta, 0)))

    def add_style(self, *, bold: bool = False, number_format: int = 0, border: bool = False) -> int:
        """Return a cellXfs index for this combination, adding it to styles.xml the first time.

        number_format is a built-in format id (14 = short date).
        """
        key = (bold, number_format, border)
        if key in self._style_ids:
            return self._style_ids[key]
        if self._styles is None:
            if STYLES_PATH not in self._zip.namelist():
                raise XlsxError("The workbook has no styles part")
            self._styles = self._xml(STYLES_PATH)
        root = self._styles
        font_id = self._bold_font_id(root) if bold else 0
        border_id = self._thin_border_id(root) if border else 0

        xfs = root.find(q("cellXfs"))
        xf = etree.SubElement(xfs, q("xf"), numFmtId=str(number_format), fontId=str(font_id), fillId="0",
                              borderId=str(border_id), xfId="0")
        for flag, attribute in ((number_format, "applyNumberFormat"), (bold, "applyFont"), (border, "applyBorder")):
            if flag:
                xf.set(attribute, "1")
        xfs.set("count", str(len(xfs.findall(q("xf")))))
        self._style_ids[key] = len(xfs.findall(q("xf"))) - 1
        return self._style_ids[key]

    def _bold_font_id(self, root: etree._Element) -> int:
        if "bold_font" not in self._style_ids:
            fonts = root.find(q("fonts"))
            font = copy.deepcopy(fonts.findall(q("font"))[0])  # the workbook's default font, made bold
            for old in font.findall(q("b")):
                font.remove(old)
            font.insert(0, etree.Element(q("b")))
            fonts.append(font)
            fonts.set("count", str(len(fonts.findall(q("font")))))
            self._style_ids["bold_font"] = len(fonts.findall(q("font"))) - 1
        return self._style_ids["bold_font"]

    def _thin_border_id(self, root: etree._Element) -> int:
        if "thin_border" not in self._style_ids:
            borders = root.find(q("borders"))
            border = etree.SubElement(borders, q("border"))
            for side in ("left", "right", "top", "bottom"):
                etree.SubElement(etree.SubElement(border, q(side), style="thin"), q("color"), indexed="64")
            etree.SubElement(border, q("diagonal"))
            borders.set("count", str(len(borders.findall(q("border")))))
            self._style_ids["thin_border"] = len(borders.findall(q("border"))) - 1
        return self._style_ids["thin_border"]

    def cell_text(self, sheet_name: str, ref: str) -> str | None:
        """The displayed text of a value cell (numbers as written in the XML, shared strings resolved)."""
        cell_type, text = self.sheet(sheet_name).raw_value(ref)
        if cell_type == "s" and text is not None:
            return self.shared_strings()[int(text)]
        return text

    def shared_strings(self) -> list[str]:
        """Shared string table, parsed with lxml (much faster than openpyxl for large tables)."""
        if self._shared_strings is None:
            self._shared_strings = []
            if "xl/sharedStrings.xml" in self._zip.namelist():
                root = etree.fromstring(self._read("xl/sharedStrings.xml"), _PARSER)
                for item in root.iter(q("si")):
                    # Rich-text runs (<r><t>) are joined; phonetic hints (<rPh>) are skipped.
                    self._shared_strings.append("".join(
                        t.text or "" for t in item.iter(q("t")) if t.getparent().tag != q("rPh")))
        return self._shared_strings

    def set_full_calc_on_load(self) -> None:
        root = etree.fromstring(self._read("xl/workbook.xml"), _PARSER)
        calc = root.find(q("calcPr"))
        if calc is None:
            calc = etree.Element(q("calcPr"))
            later = next((root.find(q(tag)) for tag in _AFTER_CALC_PR if root.find(q(tag)) is not None), None)
            if later is not None:
                later.addprevious(calc)
            else:
                root.append(calc)
        calc.set("fullCalcOnLoad", "1")
        self._replaced["xl/workbook.xml"] = etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)

    def drop_calc_chain(self) -> None:
        """Remove calcChain.xml (Excel rebuilds it). Needed after formulas are removed from cells."""
        if "xl/calcChain.xml" not in self._zip.namelist():
            return
        self._removed.add("xl/calcChain.xml")
        rels = etree.fromstring(self._read("xl/_rels/workbook.xml.rels"), _PARSER)
        for rel in list(rels):
            if rel.get("Type") == CALC_CHAIN_TYPE:
                rels.remove(rel)
        self._replaced["xl/_rels/workbook.xml.rels"] = etree.tostring(rels, xml_declaration=True, encoding="UTF-8", standalone=True)
        types = etree.fromstring(self._read("[Content_Types].xml"), _PARSER)
        for override in list(types):
            if override.get("PartName") == "/xl/calcChain.xml":
                types.remove(override)
        self._replaced["[Content_Types].xml"] = etree.tostring(types, xml_declaration=True, encoding="UTF-8", standalone=True)

    def save(self) -> bytes:
        for key, editor in self._sheets.items():
            if editor.changed:
                self._replaced[self._sheet_paths[key]] = editor.to_bytes()
        if self._styles is not None:
            self._put(STYLES_PATH, self._styles)
        out = BytesIO()
        existing = set(self._zip.namelist())
        with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED) as target:
            for info in self._zip.infolist():
                if info.filename in self._removed:
                    continue
                data = self._replaced.get(info.filename)
                if data is None:
                    data = self._zip.read(info.filename)
                target.writestr(info, data, compress_type=info.compress_type)
            for name, data in self._replaced.items():  # new parts, such as an added sheet
                if name not in existing:
                    target.writestr(name, data)
        return out.getvalue()
