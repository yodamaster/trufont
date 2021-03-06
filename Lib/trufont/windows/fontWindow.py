from defconQt.controls.glyphCellView import GlyphCellView, GlyphCellWidget
from defconQt.windows.baseWindows import BaseMainWindow
from trufont.controls.fontDialogs import AddGlyphsDialog, SortDialog
from trufont.objects import settings
from trufont.objects.defcon import TFont
from trufont.objects.menu import Entries
from trufont.tools import errorReports, platformSpecific
from trufont.windows.fontFeaturesWindow import FontFeaturesWindow
from trufont.windows.fontInfoWindow import FontInfoWindow
from trufont.windows.glyphWindow import GlyphWindow
from trufont.windows.groupsWindow import GroupsWindow
from trufont.windows.metricsWindow import MetricsWindow
from trufont.windows.settingsWindow import SettingsWindow
from PyQt5.QtCore import QEvent, QMimeData, QSize, Qt
from PyQt5.QtGui import QCursor, QKeySequence
from PyQt5.QtWidgets import (
    QApplication, QFileDialog, QLabel, QMessageBox, QSlider, QToolTip)
from collections import OrderedDict
import os
import pickle


class FontWindow(BaseMainWindow):

    def __init__(self, font, parent=None):
        super().__init__(parent)
        self._font = None

        self._settingsWindow = None
        self._infoWindow = None
        self._featuresWindow = None
        self._metricsWindow = None
        self._groupsWindow = None

        self.glyphCellView = FontCellView(self)
        self.glyphCellView.glyphActivated.connect(self._glyphActivated)
        self.glyphCellView.glyphsDropped.connect(self._orderChanged)
        self.glyphCellView.selectionChanged.connect(self._selectionChanged)
        self.glyphCellView.setAcceptDrops(True)
        self.glyphCellView.setCellRepresentationName("TruFont.GlyphCell")
        self.glyphCellView.setFocus()

        self.cellSizeSlider = QSlider(Qt.Horizontal, self)
        self.cellSizeSlider.setMinimum(32)
        self.cellSizeSlider.setMaximum(116)
        self.cellSizeSlider.setFixedWidth(.9 * self.cellSizeSlider.width())
        self.cellSizeSlider.sliderReleased.connect(self.writeSettings)
        self.cellSizeSlider.valueChanged.connect(self._sliderCellSizeChanged)
        self.selectionLabel = QLabel(self)

        statusBar = self.statusBar()
        statusBar.addPermanentWidget(self.cellSizeSlider)
        statusBar.addWidget(self.selectionLabel)
        statusBar.setSizeGripEnabled(False)
        if platformSpecific.needsTighterMargins():
            margins = (6, -4, 9, -3)
        else:
            margins = (2, 0, 8, 0)
        statusBar.setContentsMargins(*margins)

        self.setFont_(font)

        app = QApplication.instance()
        app.dispatcher.addObserver(self, "_fontSaved", "fontSaved")

        self.setCentralWidget(self.glyphCellView)
        self.setWindowTitle()

        self.readSettings()

    def readSettings(self):
        geometry = settings.fontWindowGeometry()
        if geometry:
            self.restoreGeometry(geometry)
        cellSize = settings.glyphCellSize()
        self.cellSizeSlider.setValue(cellSize)
        self.cellSizeSlider.valueChanged.emit(cellSize)

    def writeSettings(self):
        settings.setFontWindowGeometry(self.saveGeometry())
        settings.setGlyphCellSize(self.cellSizeSlider.value())

    def setupMenu(self, menuBar):
        app = QApplication.instance()

        fileMenu = menuBar.fetchMenu(Entries.File)
        fileMenu.fetchAction(Entries.File_New)
        fileMenu.fetchAction(Entries.File_Open)
        fileMenu.fetchMenu(Entries.File_Open_Recent)
        # TODO
        # if not platformSpecific.mergeOpenAndImport():
        fileMenu.fetchAction(Entries.File_Import, self.importFile)
        fileMenu.addSeparator()
        fileMenu.fetchAction(Entries.File_Save, self.saveFile)
        fileMenu.fetchAction(Entries.File_Save_As, self.saveFileAs)
        fileMenu.fetchAction(Entries.File_Reload, self.reloadFile)
        fileMenu.addSeparator()
        fileMenu.fetchAction(Entries.File_Export, self.exportFile)
        fileMenu.fetchAction(Entries.File_Exit)

        editMenu = menuBar.fetchMenu(Entries.Edit)
        self._undoAction = editMenu.fetchAction(Entries.Edit_Undo, self.undo)
        self._redoAction = editMenu.fetchAction(Entries.Edit_Redo, self.redo)
        editMenu.addSeparator()
        cut = editMenu.fetchAction(Entries.Edit_Cut, self.cut)
        copy = editMenu.fetchAction(Entries.Edit_Copy, self.copy)
        copyComponent = editMenu.fetchAction(
            Entries.Edit_Copy_As_Component, self.copyAsComponent)
        paste = editMenu.fetchAction(Entries.Edit_Paste, self.paste)
        self._clipboardActions = (cut, copy, copyComponent, paste)
        editMenu.addSeparator()
        editMenu.fetchAction(Entries.Edit_Settings, self.settings)

        fontMenu = menuBar.fetchMenu(Entries.Font)
        fontMenu.fetchAction(Entries.Font_Font_Info, self.fontInfo)
        fontMenu.fetchAction(Entries.Font_Font_Features, self.fontFeatures)
        fontMenu.addSeparator()
        fontMenu.fetchAction(Entries.Font_Add_Glyphs, self.addGlyphs)
        fontMenu.fetchAction(Entries.Font_Sort, self.sortGlyphs)

        menuBar.fetchMenu(Entries.Scripts)

        windowMenu = menuBar.fetchMenu(Entries.Window)
        windowMenu.fetchAction(Entries.Window_Inspector)
        windowMenu.addSeparator()
        windowMenu.fetchAction(Entries.Window_Groups, self.groups)
        windowMenu.fetchAction(Entries.Window_Metrics, self.metrics)
        windowMenu.fetchAction(Entries.Window_Scripting)
        windowMenu.addSeparator()
        action = windowMenu.fetchAction(Entries.Window_Output)
        action.setEnabled(app.outputWindow is not None)

        helpMenu = menuBar.fetchMenu(Entries.Help)
        helpMenu.fetchAction(Entries.Help_Documentation)
        helpMenu.fetchAction(Entries.Help_Report_An_Issue)
        helpMenu.addSeparator()
        helpMenu.fetchAction(Entries.Help_About)

        self._updateGlyphActions()

    # --------------
    # Custom methods
    # --------------

    def font_(self):
        return self._font

    def setFont_(self, font):
        if self._font is not None:
            self._font.removeObserver(self, "Font.Changed")
            self._font.removeObserver(self, "Font.GlyphOrderChanged")
            self._font.removeObserver(self, "Font.SortDescriptorChanged")
        self._font = font
        if font is None:
            return
        self._updateGlyphsFromGlyphOrder()
        font.addObserver(self, "_fontChanged", "Font.Changed")
        font.addObserver(
            self, "_glyphOrderChanged", "Font.GlyphOrderChanged")
        font.addObserver(
            self, "_sortDescriptorChanged", "Font.SortDescriptorChanged")

    def maybeSaveBeforeExit(self):
        if self._font.dirty:
            currentFont = self.windowTitle()[3:]
            body = self.tr("Do you want to save the changes you made "
                           "to “{}”?").format(currentFont)
            closeDialog = QMessageBox(
                QMessageBox.Question, None, body,
                QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel,
                self)
            closeDialog.setInformativeText(
                self.tr("Your changes will be lost if you don’t save them."))
            closeDialog.setModal(True)
            ret = closeDialog.exec_()
            if ret == QMessageBox.Save:
                self.saveFile()
                return True
            elif ret == QMessageBox.Discard:
                return True
            return False
        return True

    # -------------
    # Notifications
    # -------------

    # app

    def _fontSaved(self, notification):
        if notification.data["font"] != self._font:
            return
        self.setWindowModified(False)

    # widgets

    def _sliderCellSizeChanged(self):
        cellSize = self.cellSizeSlider.value()
        self.glyphCellView.setCellSize(cellSize)
        QToolTip.showText(QCursor.pos(), str(cellSize), self)

    def _glyphActivated(self, glyph):
        glyphWindow = GlyphWindow(glyph, self)
        glyphWindow.show()

    def _orderChanged(self):
        # TODO: reimplement when we start showing glyph subsets
        glyphs = self.glyphCellView.glyphs()
        self._font.glyphOrder = [glyph.name for glyph in glyphs]

    def _selectionChanged(self):
        # currentGlyph
        lastSelectedGlyph = self.glyphCellView.lastSelectedGlyph()
        app = QApplication.instance()
        app.setCurrentGlyph(lastSelectedGlyph)
        # selection text
        # TODO: this should probably be internal to the label
        selection = self.glyphCellView.selection()
        if selection is not None:
            count = len(selection)
            if count == 1:
                glyph = self.glyphCellView.glyphsForIndexes(selection)[0]
                text = "%s " % glyph.name
            else:
                text = ""
            if count:
                text = self.tr("{0}(%n selected)".format(text), n=count)
        else:
            text = ""
        self.selectionLabel.setText(text)
        # actions
        self._updateGlyphActions()

    # defcon

    def _fontChanged(self, notification):
        font = notification.object
        self.setWindowModified(font.dirty)

    def _glyphOrderChanged(self, notification):
        self._updateGlyphsFromGlyphOrder()

    def _updateGlyphsFromGlyphOrder(self):
        font = self._font
        glyphOrder = font.glyphOrder
        if glyphOrder:
            glyphCount = 0
            glyphs = []
            for glyphName in glyphOrder:
                if glyphName in font:
                    glyph = font[glyphName]
                    glyphCount += 1
                else:
                    glyph = font.newStandardGlyph(glyphName, asTemplate=True)
                glyphs.append(glyph)
            if glyphCount < len(font):
                # if some glyphs in the font are not present in the glyph
                # order, loop again to add them at the end
                for glyph in font:
                    if glyph not in glyphs:
                        glyphs.append(glyph)
                font.disableNotifications(observer=self)
                font.glyphOrder = [glyph.name for glyph in glyphs]
                font.enableNotifications(observer=self)
        else:
            glyphs = list(font)
            font.disableNotifications(observer=self)
            font.glyphOrder = [glyph.name for glyph in glyphs]
            font.enableNotifications(observer=self)
        self.glyphCellView.setGlyphs(glyphs)

    def _sortDescriptorChanged(self, notification):
        font = notification.object
        descriptors = notification.data["newValue"]
        if descriptors[0]["type"] == "glyphSet":
            glyphNames = descriptors[0]["glyphs"]
        else:
            glyphNames = font.unicodeData.sortGlyphNames(
                font.keys(), descriptors)
        font.glyphOrder = glyphNames

    # ------------
    # Menu methods
    # ------------

    # File

    def importFile(self):
        # TODO: systematize this
        fileFormats = (
            self.tr("OpenType Font file {}").format("(*.otf *.ttf)"),
            self.tr("Type1 Font file {}").format("(*.pfa *.pfb)"),
            self.tr("ttx Font file {}").format("(*.ttx)"),
            self.tr("WOFF Font file {}").format("(*.woff)"),
            self.tr("All supported files {}").format(
                "(*.otf *.pfa *.pfb *.ttf *.ttx *.woff)"),
            self.tr("All files {}").format("(*.*)"),
        )

        path, _ = QFileDialog.getOpenFileName(
            self, self.tr("Import File"), None,
            ";;".join(fileFormats), fileFormats[-2])

        if path:
            font = TFont()
            try:
                font.extract(path)
            except Exception as e:
                errorReports.showCriticalException(e)
                return
            window = FontWindow(font)
            window.show()

    def saveFile(self, path=None, ufoFormatVersion=3):
        if path is None and self._font.path is None:
            self.saveFileAs()
        else:
            if path is None:
                path = self._font.path
            self._font.save(path, ufoFormatVersion)

    def saveFileAs(self):
        fileFormats = OrderedDict([
            (self.tr("UFO Font version 3 {}").format("(*.ufo)"), 3),
            (self.tr("UFO Font version 2 {}").format("(*.ufo)"), 2),
        ])
        # TODO: switch to directory on platforms that need it
        dialog = QFileDialog(
            self, self.tr("Save File"), None, ";;".join(fileFormats.keys()))
        dialog.setAcceptMode(QFileDialog.AcceptSave)
        ok = dialog.exec_()
        if ok:
            nameFilter = dialog.selectedNameFilter()
            path = dialog.selectedFiles()[0]
            self.saveFile(path, fileFormats[nameFilter])
            self.setWindowTitle()
        # return ok

    def reloadFile(self):
        font = self._font
        if font.path is None:
            return
        font.reloadInfo()
        font.reloadKerning()
        font.reloadGroups()
        font.reloadFeatures()
        font.reloadLib()
        font.reloadGlyphs(font.keys())
        self.setWindowModified(False)

    def exportFile(self):
        path, _ = QFileDialog.getSaveFileName(
            self, self.tr("Export File"), None,
            self.tr("OpenType PS font {}").format("(*.otf)"))
        if path:
            try:
                self._font.export(path)
            except Exception as e:
                errorReports.showCriticalException(e)

    # Edit

    def undo(self):
        glyph = self.glyphCellView.lastSelectedGlyph()
        glyph.undo()

    def redo(self):
        glyph = self.glyphCellView.lastSelectedGlyph()
        glyph.redo()

    def cut(self):
        self.copy()
        glyphs = self.glyphCellView.glyphs()
        for index in self.glyphCellView.selection():
            glyph = glyphs[index]
            glyph.clear()

    def copy(self):
        glyphs = self.glyphCellView.glyphs()
        pickled = []
        for index in sorted(self.glyphCellView.selection()):
            pickled.append(glyphs[index].serialize(
                blacklist=("name", "unicode")
            ))
        clipboard = QApplication.clipboard()
        mimeData = QMimeData()
        mimeData.setData("application/x-trufont-glyph-data",
                         pickle.dumps(pickled))
        clipboard.setMimeData(mimeData)

    def copyAsComponent(self):
        glyphs = self.glyphCellView.glyphs()
        pickled = []
        for index in self.glyphCellView.selection():
            glyph = glyphs[index]
            componentGlyph = glyph.__class__()
            componentGlyph.width = glyph.width
            component = componentGlyph.instantiateComponent()
            component.baseGlyph = glyph.name
            pickled.append(componentGlyph.serialize())
        clipboard = QApplication.clipboard()
        mimeData = QMimeData()
        mimeData.setData("application/x-trufont-glyph-data",
                         pickle.dumps(pickled))
        clipboard.setMimeData(mimeData)

    def paste(self):
        clipboard = QApplication.clipboard()
        mimeData = clipboard.mimeData()
        if mimeData.hasFormat("application/x-trufont-glyph-data"):
            data = pickle.loads(mimeData.data(
                "application/x-trufont-glyph-data"))
            selection = self.glyphCellView.selection()
            glyphs = self.glyphCellView.glyphsForIndexes(selection)
            if len(data) == len(glyphs):
                for pickled, glyph in zip(data, glyphs):
                    # XXX: prune
                    glyph.prepareUndo()
                    glyph.deserialize(pickled)

    def settings(self):
        if self._settingsWindow is not None and \
                self._settingsWindow.isVisible():
            self._settingsWindow.raise_()
        else:
            self._settingsWindow = SettingsWindow(self)
            self._settingsWindow.show()

    # Font

    def fontInfo(self):
        # If a window is already opened, bring it to the front, else spawn one.
        # TODO: see about using widget.setAttribute(Qt.WA_DeleteOnClose)
        # otherwise it seems we're just leaking memory after each close...
        # (both raise_ and show allocate memory instead of using the hidden
        # widget it seems)
        if self._infoWindow is not None and self._infoWindow.isVisible():
            self._infoWindow.raise_()
        else:
            self._infoWindow = FontInfoWindow(self._font, self)
            self._infoWindow.show()

    def fontFeatures(self):
        # TODO: see up here
        if self._featuresWindow is not None and self._featuresWindow.isVisible(
                ):
            self._featuresWindow.raise_()
        else:
            self._featuresWindow = FontFeaturesWindow(self._font, self)
            self._featuresWindow.show()

    def addGlyphs(self):
        glyphs = self.glyphCellView.glyphs()
        newGlyphNames, params, ok = AddGlyphsDialog.getNewGlyphNames(
            self, glyphs)
        if ok:
            sortFont = params.pop("sortFont")
            for name in newGlyphNames:
                glyph = self._font.newStandardGlyph(name, **params)
                if glyph is not None:
                    glyphs.append(glyph)
            self.glyphCellView.setGlyphs(glyphs)
            if sortFont:
                # TODO: when the user add chars from a glyphSet and no others,
                # should we try to sort according to that glyphSet?
                # The above would probably warrant some rearchitecturing.
                # kick-in the sort mechanism
                self._font.sortDescriptor = self._font.sortDescriptor

    def sortGlyphs(self):
        sortDescriptor, ok = SortDialog.getDescriptor(
            self, self._font.sortDescriptor)
        if ok:
            self._font.sortDescriptor = sortDescriptor

    # Window

    def groups(self):
        # TODO: see up here
        if self._groupsWindow is not None and self._groupsWindow.isVisible():
            self._groupsWindow.raise_()
        else:
            self._groupsWindow = GroupsWindow(self._font, self)
            self._groupsWindow.show()

    def metrics(self):
        # TODO: see up here
        if self._metricsWindow is not None and self._metricsWindow.isVisible():
            self._metricsWindow.raise_()
        else:
            self._metricsWindow = MetricsWindow(self._font, parent=self)
            self._metricsWindow.show()
        # TODO: default string kicks-in on the window before this. Figure out
        # how to make a clean interface
        selection = self.glyphCellView.selection()
        if selection:
            glyphs = self.glyphCellView.glyphsForIndexes(selection)
            self._metricsWindow.setGlyphs(glyphs)

    # update methods

    def _updateGlyphActions(self):
        if not hasattr(self, "_undoAction"):
            return
        currentGlyph = self.glyphCellView.lastSelectedGlyph()
        # disconnect eventual signal of previous glyph
        self._undoAction.disconnect()
        self._undoAction.triggered.connect(self.undo)
        self._redoAction.disconnect()
        self._redoAction.triggered.connect(self.redo)
        # now update status
        if currentGlyph is None:
            self._undoAction.setEnabled(False)
            self._redoAction.setEnabled(False)
        else:
            undoManager = currentGlyph.undoManager
            self._undoAction.setEnabled(currentGlyph.canUndo())
            undoManager.canUndoChanged.connect(self._undoAction.setEnabled)
            self._redoAction.setEnabled(currentGlyph.canRedo())
            undoManager.canRedoChanged.connect(self._redoAction.setEnabled)
        # and other actions
        for action in self._clipboardActions:
            action.setEnabled(currentGlyph is not None)

    # ----------
    # Qt methods
    # ----------

    def sizeHint(self):
        return QSize(860, 590)

    def moveEvent(self, event):
        self.writeSettings()

    resizeEvent = moveEvent

    def showEvent(self, event):
        app = QApplication.instance()
        data = dict(
            font=self._font,
            window=self,
        )
        app.postNotification("fontWindowWillOpen", data)
        super().showEvent(event)
        app.postNotification("fontWindowOpened", data)

    def closeEvent(self, event):
        ok = self.maybeSaveBeforeExit()
        if ok:
            app = QApplication.instance()
            data = dict(
                font=self._font,
                window=self,
            )
            app.postNotification("fontWindowWillClose", data)
            self._font.removeObserver(self, "Font.Changed")
            app = QApplication.instance()
            app.dispatcher.removeObserver(self, "preferencesChanged")
            app.dispatcher.removeObserver(self, "fontSaved")
            event.accept()
        else:
            event.ignore()

    def event(self, event):
        if event.type() == QEvent.WindowActivate:
            app = QApplication.instance()
            app.setCurrentMainWindow(self)
            inspector = app.inspectorWindow
            if inspector is not None and inspector.isVisible():
                inspector.raise_()
            lastSelectedGlyph = self.glyphCellView.lastSelectedGlyph()
            if lastSelectedGlyph is not None:
                app.setCurrentGlyph(lastSelectedGlyph)
        return super().event(event)

    def setWindowTitle(self, title=None):
        if title is None:
            if self._font.path is not None:
                title = os.path.basename(self._font.path.rstrip(os.sep))
            else:
                title = self.tr("Untitled.ufo")
        super().setWindowTitle("[*]{}".format(title))


class FontCellWidget(GlyphCellWidget):

    def _proceedWithDeletion(self, erase=False):
        if not self._selection:
            return
        tr = self.tr("Delete") if erase else self.tr("Clear")
        text = self.tr("Do you want to %s selected glyphs?") % tr.lower()
        closeDialog = QMessageBox(
            QMessageBox.Question, "",
            self.tr("%s glyphs") % tr,
            QMessageBox.Yes | QMessageBox.No, self)
        closeDialog.setInformativeText(text)
        closeDialog.setModal(True)
        ret = closeDialog.exec_()
        if ret == QMessageBox.Yes:
            return True
        return False

    def keyPressEvent(self, event):
        modifiers = event.modifiers()
        if platformSpecific.isDeleteEvent(event):
            erase = modifiers & Qt.ShiftModifier
            if self._proceedWithDeletion(erase):
                glyphs = self.glyphsForIndexes(self._selection)
                for glyph in glyphs:
                    font = glyph.font
                    if erase:
                        del font[glyph.name]
                    else:
                        # TODO: consider doing that in glyph template setter
                        glyph.clear()
                        glyph.template = True
        elif event.matches(QKeySequence.SelectAll):
            self.selectAll()
        elif event.key() == Qt.Key_D and modifiers & Qt.ControlModifier:
            self.setSelection(set())
        else:
            super().keyPressEvent(event)


class FontCellView(GlyphCellView):
    glyphCellWidgetClass = FontCellWidget
