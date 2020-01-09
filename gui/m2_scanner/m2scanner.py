# -*- coding: utf-8 -*-
"""
This module contains a GUI for operating the spectrum logic module.

Qudi is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.

Qudi is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU General Public License for more details.

You should have received a copy of the GNU General Public License
along with Qudi. If not, see <http://www.gnu.org/licenses/>.

Copyright (c) the Qudi Developers. See the COPYRIGHT.txt file at the
top-level directory of this distribution and at <https://github.com/Ulm-IQO/qudi/>
"""

import time
import os
import pyqtgraph as pg
import numpy as np

from core.module import Connector
from core.util import units
from gui.colordefs import QudiPalettePale as palette
from gui.guibase import GUIBase
from gui.fitsettings import FitSettingsDialog, FitSettingsComboBox
from qtpy import QtCore
from qtpy import QtWidgets
from qtpy import uic


class M2ControllerWindow(QtWidgets.QMainWindow):

    def __init__(self):
        """ Create the laser scanner window.
        """
        # Get the path to the *.ui file
        this_dir = os.path.dirname(__file__)
        ui_file = os.path.join(this_dir, 'ui_m2scanner_withfit4.ui') #modified

        # Load it
        super().__init__()
        uic.loadUi(ui_file, self)
        self.show()


class M2ScannerGUI(GUIBase):
    _modclass = 'M2LaserGui'
    _modtype = 'gui'

    sigStartCounter = QtCore.Signal()
    sigStopCounter = QtCore.Signal()

    sigFitChanged = QtCore.Signal(str)
    sigDoFit = QtCore.Signal()


    # declare connectors
    laserlogic = Connector(interface='M2LaserLogic')

    def __init__(self, config, **kwargs):
        super().__init__(config=config, **kwargs)

    def on_activate(self):
        """ Definition and initialisation of the GUI.
        """

        #Connect to laser_logic
        self._laser_logic = self.laserlogic()

        # setting up the window
        self._mw = M2ControllerWindow()


        ###################
        # Connect Signals
        ###################

        self._mw.run_scan_Action.triggered.connect(self.start_clicked)  # start_clicked then triggers sigStartCounter
        self._mw.save_scan_Action.triggered.connect(self.save_spectrum_data)  # start_clicked then triggers sigStartCounter
        self._mw.actionSave_as.triggered.connect(self.change_filepath)
        #      self._mw.restore_default_view_Action.triggered.connect(self.restore_default_view)

        # FROM countergui.py
        # Connect signals for counter
        self.sigStartCounter.connect(self._laser_logic.startCount)
        self.sigStopCounter.connect(self._laser_logic.stopCount)
        self._laser_logic.sigScanComplete.connect(self.scanComplete) #handle the end of scans

        # Handling signals from the logic
        #   signals during terascan
        self._laser_logic.sigCounterUpdated.connect(self.update_data)
        #   update wavelength reading and status reading only
        self._laser_logic.sigUpdate.connect(self.updateGui)


        ####################
        #set up GUI
        ####################

        self._mw.scanType_comboBox.setInsertPolicy = 6  # InsertAlphabetically
        self._mw.scanType_comboBox.addItem("Fine")
        self._mw.scanType_comboBox.addItem("Medium")

        self._mw.scanRate_comboBox.setInsertPolicy = 6 #InsertAlphabetically
        for x in range(0, 14):
            self._mw.scanRate_comboBox.addItem(str(x))



        #####################
        # Connecting user interactions
        ########################

        self._mw.scanType_comboBox.currentIndexChanged.connect(self.update_calculated_scan_params)
        self._mw.scanRate_comboBox.currentIndexChanged.connect(self.update_calculated_scan_params)
        self._mw.startWvln_doubleSpinBox.valueChanged.connect(self.update_calculated_scan_params)
        self._mw.stopWvln_doubleSpinBox.valueChanged.connect(self.update_calculated_scan_params)
        self._mw.numScans_spinBox.valueChanged.connect(self.update_calculated_scan_params)

        self._mw.plotPoints_checkBox.stateChanged.connect(self.update_points_checkbox)
        self._mw.replot_pushButton.clicked.connect(self.replot_pressed)

        #below from countergui.py
        self._pw = self._mw.plotWidget
        self._plot_item = self._pw.plotItem

        # create a new ViewBox, link the right axis to its coordinate system
        self._right_axis = pg.ViewBox()
        self._plot_item.showAxis('right')
        self._plot_item.scene().addItem(self._right_axis)
        self._plot_item.getAxis('right').linkToView(self._right_axis)
        self._right_axis.setXLink(self._plot_item)

        # create a new ViewBox, link the right axis to its coordinate system
        self._top_axis = pg.ViewBox()
        self._plot_item.showAxis('top')
        self._plot_item.scene().addItem(self._top_axis)
        self._plot_item.getAxis('top').linkToView(self._top_axis)
        self._top_axis.setYLink(self._plot_item)
        self._top_axis.invertX(b=True)


        self._pw.setLabel('left', 'Count Rate', units='counts/s')
        ##self._pw.setLabel('right', 'Counts', units='#') #Tget rid of or implement
        self._pw.setLabel('bottom', 'Wavelength', units='nm')
        ##self._pw.setLabel('top', 'Relative Frequency', units='Hz') #TODO implement

         # Create an empty plot curve to be filled later, set its pen
        self._curve1 = self._pw.plot()
        self._curve1.setPen(palette.c1, width=2)

        #self._curve1 = pg.PlotDataItem(
        #        pen=pg.mkPen(palette.c3, style=QtCore.Qt.DotLine),
        #        symbol='s',
        #        symbolPen=palette.c3,
        #        symbolBrush=palette.c3,
        #        symbolSize=5)
        self._pw.addItem(self._curve1)

        #initialize starting calculated scanning parameters
        self.update_calculated_scan_params()  # initialize

        #
        self._mw.wvlnRead_disp.setText("Laser Connected")
        self._mw.status_disp.setText("idle")

        #show the main gui
        self._mw.show()

        # fit settings #just added!
        self._fsd = FitSettingsDialog(self._laser_logic.fc)
        self._fsd.sigFitsUpdated.connect(self._mw.fit_methods_ComboBox.setFitFunctions)
        self._fsd.applySettings()

        self._mw.action_FitSettings.triggered.connect(self._fsd.show)
        self._mw.do_fit_PushButton.clicked.connect(self.doFit)
        self.sigDoFit.connect(self._laser_logic.do_fit)
        self.sigFitChanged.connect(self._laser_logic.fc.set_current_fit)
        self._laser_logic.sig_fit_updated.connect(self.updateFit) #TODO

        self.curve_fit = pg.PlotDataItem(
            pen=pg.mkPen(palette.c2, width=3),
            symbol=None
            )


    def on_deactivate(self):
        """ Deinitialisation performed during deactivation of the module.
        """
        # disconnect signals #I think this will be unnecessary given that only the gui,
        #not hardware or logic modules will deactivate when deactivate button is pressed
        #(modeled after previously existing Laser module)
#        self._fsd.sigFitsUpdated.disconnect()

        print('in gui trying to deactivate')
        self._mw.close()

        #if a terascan is running, stop the terascan before deactivating
        if self._laser_logic.module_state() == 'locked':
            print('Terascan is running. Trying to stop now!')
            self._mw.run_scan_Action.setText('Start counter')
            self.sigStopCounter.emit()

            startWvln, stopWvln, scantype, scanrate, numScans = self.get_scan_info()
            self._laser_logic._laser.stop_terascan(scantype, True)



    def show(self):
        """Make window visible and put it above all other windows.
        """
        QtWidgets.QMainWindow.show(self._mw)
        self._mw.activateWindow()
        self._mw.raise_()

    def update_data(self):
        """ The function that grabs the terascan count data and sends it to the plot.
        """
#        print('update_data called in gui')
        ################ Adapted from spectrometer gui
        data = self._laser_logic.countdata

        #Don't plot initialization 0's
        mask = (data==0).all(0)
        start_idx = np.argmax(~mask)
        data = data[:,start_idx:]

        # draw new data
        if data.shape[1] > 0:
            self._curve1.setData(x=data[0, :], y=data[1, :])
 #       print('updatedata finished in gui')


    def save_spectrum_data(self):
        self._laser_logic.save_spectrum_data()


    def get_scan_info(self):
#        print('get_scan_info called in gui')
        finerates = [20, 10, 5, 2, 1, .5, .2, .1, .05, .02, .01, .005, .002, .001]  # in GHz/s
        mediumrates = [100, 50, 20, 15, 10, 5, 2, 1]  # in GHz/s

        typebox = self._mw.scanType_comboBox.currentText()
        if typebox== 'Fine':
            scanrate = finerates[int(self._mw.scanRate_comboBox.currentText())] * 10 ** 9  # in Hz/s
        else:
            indx = int(self._mw.scanRate_comboBox.currentText())
            #handle if index is too high for medium scan
            if indx >= len(mediumrates):
                error_dialog = QtWidgets.QErrorMessage()
                error_dialog.showMessage('ERROR: index given for medium rates is too high')
                error_dialog.exec()
                self._mw.scanRate_comboBox.setCurrentIndex(7)
                return
                #alternatively (Todo?) change number of scanRate options based on whether we are on Fine or Medium
            else:
                scanrate = mediumrates[int(self._mw.scanRate_comboBox.currentText())] * 10 ** 9  # in Hz/s

        startwvln = self._mw.startWvln_doubleSpinBox.value() * 10 ** -9  # in m
        stopwvln = self._mw.stopWvln_doubleSpinBox.value() * 10**-9 #in m

        numscans = self._mw.numScans_spinBox.value()

        return startwvln, stopwvln, typebox.lower(), scanrate, numscans


    def update_calculated_scan_params(self):
        speed_c = 299792458 #speed of light in m/s

        try:
            startWvln, stopWvln, scantype, scanrate, numScans = self.get_scan_info()
        except:
            return #error handling occurs inside get_scan_info

        midWvln = (stopWvln + startWvln)/2 #in m
        rangeWvln = (stopWvln - startWvln) #in m

        startFreq = speed_c/ stopWvln #in Hz
        stopFreq = speed_c/ startWvln #in Hz
        midFreq = (stopFreq + startFreq)/2 #in Hz
        rangeFreq = stopFreq - startFreq #in Hz
        scanrate_wvln = scanrate * speed_c/(midFreq**2) #in m/s

        self._mw.calcDwellTime_disp.setText("0.2 sec \n{0:.4f} pm".format(0.2*scanrate_wvln*10**12))
                    #Scan resolution is 0.2 sec  (based on manually testing, with print
                    #and time.time() statements in countloop). May be different on a different computer

        self._mw.calcScanRes_disp.setText("{0:.3f} GHz/s \n{1:.3f} pm/s".format(scanrate*10**-9,scanrate_wvln*10**12))

        totaltime = numScans*rangeFreq/scanrate
        secs = totaltime%60
        mins = totaltime//60
        hrs = mins//60
        days = hrs//24

        if days > 0:
            timestr = "{0:.0f} days, {1:.0f} hrs, {2:.0f} min, {3:.0f} sec".format(days, hrs, mins, secs)
        elif hrs > 0:
            timestr = "{0:.0f} hrs, {1:.0f} min, {2:.0f} sec".format(hrs, mins, secs)
        elif mins > 0:
            timestr = "{0:.0f} min, {1:.0f} sec".format( mins, secs)
        else:
            timestr = "{0:.0f} sec".format(secs)

        self._mw.calcTotalTime_disp.setText(timestr)



    @QtCore.Slot()
    def updateGui(self):
        """ Update labels, the plot and button states with new data. """
        self._mw.wvlnRead_disp.setText("{0:.5f}".format(self._laser_logic.current_wavelength))
        self._mw.status_disp.setText(self._laser_logic.current_state)



    def start_clicked(self): #todo: move the logic elements of this function to the logic module
        """ Handling the Start button to stop and restart the counter.
        """
#        print('start_clicked called in gui')
        if self._laser_logic.module_state() == 'locked':

            print('STOP TERASCAN')

            #   Disable/enable buttons as appropriate, update gui
            self._mw.replot_pushButton.setEnabled(True)
            self._mw.run_scan_Action.setEnabled(False)

            self._mw.status_disp.setText('stopping scan') #is not being seen.? fixme
            self._laser_logic.current_state = 'stopping scan' #is not being seen fixme

            self._mw.run_scan_Action.setText('Start counter') #this also isn't working as far as I can tell fixme

            #   Stop the counter
            self.sigStopCounter.emit()

            #   Enable the "start/stop scan" button Todo maybe wait for signal before enable?
            self._mw.run_scan_Action.setEnabled(True)
        else:
            print('START TERASCAN')

            #   Disable/enable buttons as appropriate, update gui
            #self._laser_logic.current_state = 'starting scan'
            self._mw.status_disp.setText('starting scan')

            self._mw.replot_pushButton.setEnabled(False)
            self._mw.run_scan_Action.setEnabled(False)
            self._mw.run_scan_Action.setText('Stop counter') #not sure if this is working fixme

            #   Grab terascan parameters
            startWvln, stopWvln, scantype, scanrate, numScans = self.get_scan_info()

            #   More update gui
            self._mw.scanNumber_label.setText(str(self._laser_logic.repetition_count))
            self._mw.scanNumber_label.setText(
                "Scan {0:d} of {1:d}".format(self._laser_logic.repetition_count + 1, numScans))

            #   Check for input parameter errors. E.G., stop_wavelength should be less than start_wavelength
            if startWvln >= stopWvln:
                error_dialog = QtWidgets.QErrorMessage()
                error_dialog.showMessage('ERROR: start wavelength must be less than stop wavelength')
                error_dialog.exec()
                return self._laser_logic.module_state()

            #   save terascan parameters to laser module
            self._laser_logic.scanParams = {"scanbounds": (startWvln, stopWvln), "scantype":scantype,
                "scanrate":scanrate, "numScans":numScans}


            #   Start the counter
            self.sigStartCounter.emit()

            #   Enable clicking of "start/stop" button
            self._mw.run_scan_Action.setEnabled(True)

#        print('start_clicked finished in gui')
        return self._laser_logic.module_state()



    def update_points_checkbox(self):
    #Change display style of counts plot
        #check if locked?
        if not self._mw.plotPoints_checkBox.isChecked():
            self._curve1.setPen(palette.c1, width=2)
            self._curve1.setSymbol(None)
        else:
            self._curve1.setPen(palette.c3, style=QtCore.Qt.DotLine)
            self._curve1.setSymbol('s')
            self._curve1.setSymbolBrush(palette.c3)
            self._curve1.setSymbolSize(5)
            self._curve1.setSymbolPen(palette.c3)
        #self._pw.addItem(self._curve1)

    def replot_pressed(self):
    #Replot counts to be ordered by wavelength
        if self._laser_logic.module_state() == 'locked':
            pass #Button should be disabled when module_state is locked, so this should never happen anyway
        else:
            self._laser_logic.order_data()
            self.update_data()
           ### self._mw.replot_pushButton.setDefault(False) #Isn't working? Supposed to reset button style TODO fix

    def scanComplete(self):
    #Handle the end of scans
        startWvln, stopWvln, scantype, scanrate, numScans = self.get_scan_info()

        self._mw.wvlnRead_disp.setText("Scan Completed")
        self._mw.status_disp.setText("idle")

        if numScans != 1:
        # don't automatically save if it's just one scan
            #save scan. Note: save_spectrum_data increases repetition_count by 1
            self._laser_logic.save_spectrum_data()

        if numScans == self._laser_logic.repetition_count or self._laser_logic.repetition_count == 0:
        # Final scan has been completed.

            self._laser_logic.repetition_count = 0
            self._mw.replot_pushButton.setEnabled(True)

            self._laser_logic.module_state.unlock()

        else:
        # Advance to next scan

            self._mw.scanNumber_label.setText("Scan {0:d} of {1:d}".format(self._laser_logic.repetition_count + 1, numScans))

            self._laser_logic.module_state.unlock()
            self.sigStartCounter.emit() #clears out data, etc.

    def change_filepath(self):
        save_dialog = QtWidgets.QFileDialog()
        self._laser_logic.filepath = save_dialog.getExistingDirectory()
        print('Saving in ' + self._laser_logic.filepath)
        self.save_spectrum_data()

    @QtCore.Slot()
    def doFit(self):
        self.sigFitChanged.emit(self._mw.fit_methods_ComboBox.getCurrentFit()[0])
        self.sigDoFit.emit()


    @QtCore.Slot()
    def updateFit(self):
        """ Do the configured fit and show it in the plot """
        fit_name = self._laser_logic.fc.current_fit
        fit_result = self._laser_logic.fc.current_fit_result
        fit_param = self._laser_logic.fc.current_fit_param

        if fit_result is not None:
            # display results as formatted text
            self._mw.fit_results_DisplayWidget.clear()
            try:
                formated_results = units.create_formatted_output(fit_result.result_str_dict)
            except:
                formated_results = 'this fit does not return formatted results'
            self._mw.fit_results_DisplayWidget.setPlainText(formated_results)

        if fit_name is not None:
            self._mw.fit_methods_ComboBox.setCurrentFit(fit_name)

        # check which fit method is used and show the curve in the plot accordingly
        if fit_name != 'No Fit':
            self.curve_fit.setData(
                x=self._laser_logic.wlog_fit_x,
                y=self._laser_logic.wlog_fit_y)

            if self.curve_fit not in self._mw.plotWidget.listDataItems():
                self._mw.plotWidget.addItem(self.curve_fit)
        else:
            if self.curve_fit in self._mw.plotWidget.listDataItems():
                self._mw.plotWidget.removeItem(self.curve_fit)


#https://doc.qt.io/qt-5/signalsandslots.html
#When a signal is emitted, the slots connected to it are usually executed immediately, just like a normal
# function call. When this happens, the signals and slots mechanism is totally independent of any GUI event loop.
# Execution of the code following the emit statement will occur once all slots have returned. The situation
# is slightly different when using queued connections; in such a case, the code following the emit keyword
# will continue immediately, and the slots will be executed later.

#Seems wrong??? sigStartQueryLoop is connected NOT through a queued connection and the function doesn't wait
#for the slots to complete. "slot returning" must just mean that the slot has begun executing???