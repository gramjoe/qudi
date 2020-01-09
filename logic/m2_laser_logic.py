#-*- coding: utf-8 -*-
"""
Laser management.

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
import numpy as np
from qtpy import QtCore

from core.module import Connector, ConfigOption
from logic.generic_logic import GenericLogic
from logic.counter_logic import CounterLogic
from interface.simple_laser_interface import ControlMode, ShutterState, LaserState



from collections import OrderedDict
import matplotlib.pyplot as plt

from core.module import StatusVar
from logic.generic_logic import GenericLogic
from interface.slow_counter_interface import CountingMode
from core.util.mutex import Mutex


#RESOURCES:
#spectrum logic (for plot!)
#counter logic (some parts copied/pasted)
#laser logic (based on this)



class M2LaserLogic(CounterLogic):


    """ Logic module agreggating multiple hardware switches.
    """
    #Adapted from LaserLogic

    _modclass = 'm2laser'
    _modtype = 'logic'


    laser = Connector(interface='M2LaserInterface')
    # waiting time between queries im milliseconds
    queryInterval = ConfigOption('query_interval', 100) #needed for wavemeter

    sigUpdate = QtCore.Signal()
#    sigStartScan = QtCore.Signal() #todelete

    #Added from wavemeter_logger_logic
    sig_fit_updated = QtCore.Signal()

    #############    Adapted from CounterLogic
    sigCounterUpdated = QtCore.Signal()
    sigCountDataNext = QtCore.Signal()
    #To be deleted: signals below
  #  sigGatedCounterFinished = QtCore.Signal()
  #  sigGatedCounterContinue = QtCore.Signal(bool)
  #  sigCountingSamplesChanged = QtCore.Signal(int)
  #  sigCountLengthChanged = QtCore.Signal(int)
  #  sigCountFrequencyChanged = QtCore.Signal(float)
  #  sigSavingStatusChanged = QtCore.Signal(bool)
  #  sigCountStatusChanged = QtCore.Signal(bool)
  #  sigCountingModeChanged = QtCore.Signal(CountingMode)
    sigScanComplete = QtCore.Signal() #JUST ADDED, may be unnecessary

    ## declare connectors
    counter1 = Connector(interface='SlowCounterInterface')
    savelogic = Connector(interface='SaveLogic')
    fitlogic = Connector(interface='FitLogic')

    # status vars: These must be adjusted in init function (below commented lines do nothing when uncommented)
    #which is in counter_logic.py (m2_laser_logic.py extends this)
#    _smooth_window_length = StatusVar('smooth_window_length', 10) #these may not do anything
#    _counting_samples = StatusVar('counting_samples', 1)
#    _count_frequency = StatusVar('count_frequency', 50)
  #  _saving = StatusVar('saving', False)


    def on_activate(self):
        ############## Counter related on_activate tasks:
        # Connect to hardware and save logic
        print('on_activate is called in m2_laser_logic')


        self._counting_device = self.counter1()
        self._save_logic = self.savelogic()

        # Overwrite count_length from counter_logic so that we can have data longer than 300 points
        self._count_length = 100000 #1000000


        # Recall saved app-parameters
        if 'counting_mode' in self._statusVariables:
            self._counting_mode = CountingMode[self._statusVariables['counting_mode']]

        constraints = self.get_hardware_constraints()
        number_of_detectors = constraints.max_detectors

        #initialize repetition count
        self.repetition_count = 0

        #initialize current count
        self.integrated_counts = 0

        # initialize data arrays
        self.countdata = np.zeros([len(self.get_channels())+1, self._count_length])#TODO are some of these unused/can be deleted?
        self.countdata_smoothed = np.zeros([len(self.get_channels())+1, self._count_length])
        self.rawdata = np.zeros([len(self.get_channels())+1, self._counting_samples])
        self._already_counted_samples = 0  # For gated counting
        self._data_to_save = []

        # Flag to stop the loop
        self.stopRequested = False #modified -ed
        self._saving_start_time = time.time()

        # connect signals
        self.sigCountDataNext.connect(self.count_loop_body, QtCore.Qt.QueuedConnection)


        #############     Laser-related on_activate tasks
        """ Prepare logic module for work.
        """
        self._laser = self.laser()


        #set up default save_folder
        self.filepath = self._save_logic.get_path_for_module(module_name='spectra')

        #Initialize GUI with starting values
        self.current_wavelength = 'idle'
        self.current_state = 'idle'
        #call updateGui TODO


        #Prepare fit logic
        self._fit_logic = self.fitlogic()
        self.fc = self._fit_logic.make_fit_container('Wavemeter counts', '1d')
        self.fc.set_units(['Hz', 'c/s'])

        if 'fits' in self._statusVariables and isinstance(self._statusVariables['fits'], dict):
            self.fc.load_from_dict(self._statusVariables['fits'])
        else:
            d1 = OrderedDict()
            d1['Lorentzian peak'] = {
                'fit_function': 'lorentzian',
                'estimator': 'peak'
            }
            d1['Two Lorentzian peaks'] = {
                'fit_function': 'lorentziandouble',
                'estimator': 'peak'
            }
            d1['Two Gaussian peaks'] = {
                'fit_function': 'gaussiandouble',
                'estimator': 'peak'
            }
            default_fits = OrderedDict()
            default_fits['1d'] = d1
            self.fc.load_from_dict(default_fits)

        # #JUST ADDED below
        # # create a new x axis from xmin to xmax with bins points
        # self.histogram_axis = np.arange(
        #     self._xmin,
        #     self._xmax,
        #     (self._xmax - self._xmin) / self._bins
        #     )
        # self.histogram = np.zeros(self.histogram_axis.shape)
        # self.envelope_histogram = np.zeros(self.histogram_axis.shape)


    def on_deactivate(self):
        #taken from counter_logic: (not sure if neccessary?)
        """ Deinitialisation performed during deactivation of the module.
        """
        # Save parameters to disk
        self._statusVariables['counting_mode'] = self._counting_mode.name

        # Stop measurement
        if self.module_state() == 'locked':
            self._stopCount_wait()

        self.sigCountDataNext.disconnect()



    #overload from counter_logic.py
    def count_loop_body(self):
        """ This method gets the count data from the hardware for the continuous counting mode (default).

        It runs repeatedly in the logic module event loop by being connected
        to sigCountContinuousNext and emitting sigCountContinuousNext through a queued connection.
        """
     #   print('count_loop_body runs')
        if self.module_state() == 'locked': #
            with self.threadlock:
                # check for aborts of the thread in break if necessary

                if self.stopRequested: #modified -ed
                    self.current_state = 'scan stopped'
                    ##self.sigUpdate.emit() #show state change 'stopping scan'
                    # close off the actual counter
                    cnt_err = self._counting_device.close_counter()
                    clk_err = self._counting_device.close_clock()
                    if cnt_err < 0 or clk_err < 0:
                        self.log.error('Could not even close the hardware, giving up.')

                    #   Stop the terascan
                    self._laser.stop_terascan(self.scanParams["scantype"], True)


                    # switch the state variable off again
                    self.stopRequested = False #modified -ed
                    self.module_state.unlock()

       #             self.sigCounterUpdated.emit() #plot last data bits?
                    return

                #read the current wavelength value here as well, average with below val?


                #national_instruments_x_series.py is set up to return an array with a length dependent on
                #the counter clock frequency. To integrate over counts, just sum this array along the correct dimension

#                self.rawdata = self._counting_device.get_counter(samples=self._counting_samples)
             #   self.rawdata = self._counting_device.get_counter(samples=25) #Adjust As Neccessary


                #time elapsed since last call in seconds * samples logged per second
                #samples logged per second = clock rate for counter (daq)
                #0.2 sec * self._count_frequency = 0.2 sec * 50 cts per sec = 10
                #Keep in mind get_counter is a blocking function so it will block until all the counts are filled
                #Lagging in counts displayed is observed when samples supplied here was too small
                #But stalling when get_counter is called is observed when samples supplied is too large
                self.rawdata = self._counting_device.get_counter(samples=round(self._count_frequency*0.2) + 15)
                #Ideally, we figure out how to shrink the time for samples logged per second down much more so that
                #get_counter would always be the time-limiting step
                #to do this we have to change the way wavelength is read


                #print('is this an array')
                #print(self.rawdata.shape)
            ##    print(self.rawdata)

                numSamples = self.rawdata.shape[0]
                self.rawdata = np.sum(self.rawdata, axis=1)
                self.rawdata.shape = (numSamples,1)

                #potentially error I am seeing: stop is pressed while process flow is about here.
                #key error as laser scan stops but this function still searches for wavelength todo fix

             #   t1 = time.time()
                #Caution: the time it takes to read the wavelength value is approx 0.2 sec, setting wavelength msmt resolution
                wavelength, current_state = self._laser.get_terascan_wavelength()
             #   t2 = time.time()
             #   print(t2 - t1)
             #   print(wavelength)
             #   print(current_state)

                #Don't collect counts when the laser is stitching or otherwise not scanning
                if current_state == 'stitching':
            #        print('stitching')
                    self.current_wavelength = wavelength
                    self.current_state = current_state
                    self.sigUpdate.emit()
                    self.sigCountDataNext.emit()
                    return

                #Handle finished scan
                if current_state == 'complete': #timeout in get_terascan_wavelength(), LOOK AT, is there a better way to handle???? TODO
                    self.current_state = 'scan completing'
                    self.sigUpdate.emit()
                    cnt_err = self._counting_device.close_counter()
                    clk_err = self._counting_device.close_clock()
                    self.sigScanComplete.emit()
                    return

                #handle data collected from scan
                if self.rawdata[0, 0] < 0: #counts can't be negative(?)
                    self.log.error('The counting went wrong, killing the counter.')
                    self.stopRequested = True #modified -ed
                else:
                    if self._counting_mode == CountingMode['CONTINUOUS']:
                        self.current_state = current_state
                        self.current_wavelength = wavelength #do it this way to avoid blocking when updateGui is called below
                        #since updateGui needs to access self.current_wavelength but it is connected to a signal so
                        #runs asynchronously with this function which calls itself/loops
                        self._process_data_continous()
                    elif self._counting_mode == CountingMode['GATED']: #not tested
                        self._process_data_gated()
                    elif self._counting_mode == CountingMode['FINITE_GATED']: #not tested
                        self._process_data_finite_gated()
                    else:
                        self.log.error('No valid counting mode set! Can not process counter data.')


            # call this again from event loop
            self.sigCounterUpdated.emit() #this connects to m2scanner.py GUI, update_data function
            self.sigCountDataNext.emit() #this is connected to count_loop_body, so will call this func again
            #these two are essentially called in parallel (update gui and count_loop_body).
            #this is okay because they don't access the same resources. count_loop_body accesses raw_data
            #while update_gui accesses count_data (which comes from rawdata via process_data_continuous)

            self.sigUpdate.emit() #connects to updateGui to update the wavelength
       #     print('count_loop_body ended')
        return




#Overload from counter_logic so that we can save wavelength as well as counts
    def _process_data_continous(self):
        """
        Processes the raw data from the counting device
        @return:
        """

        self.countdata[0, 0] = np.average(self.current_wavelength)
        self.countdata[1, 0] = np.average(self.rawdata[0][0])


        # move the array to the left to make space for the new data
        self.countdata = np.roll(self.countdata, -1, axis=1) #Not sure if roll is what we want here
        # also move the smoothing array
        self.countdata_smoothed = np.roll(self.countdata_smoothed, -1, axis=1) #?? what is this?
        # calculate the median and save it
        window = -int(self._smooth_window_length / 2) - 1 #why/what?
        for i in range(len(self.get_channels())+1):
            self.countdata_smoothed[i, window:] = np.median(self.countdata[i,
                                                            -self._smooth_window_length:])

        # save the data if necessary
        if self._saving:
             # if oversampling is necessary #oversampling should not be relevant for the terascans
            if self._counting_samples > 1: #this block should not run.! delete?
                chans = self.get_channels()
                self._sampling_data = np.empty([len(chans) + 1, self._counting_samples])
                self._sampling_data[0, :] = time.time() - self._saving_start_time
                for i in range(len(chans)):
                    self._sampling_data[i+1, 0] = self.rawdata[i]

                self._data_to_save.extend(list(self._sampling_data))
            # if we don't want to use oversampling
            else:
                # append tuple to data stream (timestamp, average counts)
                chans = self.get_channels()
                newdata = np.empty((len(chans) + 1, ))
                newdata[0] = time.time() - self._saving_start_time
                for i in range(len(chans) +1):
                    newdata[i+1] = self.countdata[i, -1]
                self._data_to_save.append(newdata) #stuff newdata into _data_to_save class array
        return




#    @QtCore.Slot()
#    def start_terascan(self,scantype, scanbounds, scanrate):
#        self._laser.setup_terascan(scantype, tuple([1E9*x for x in scanbounds]), scanrate)
#        self._laser.start_terascan(scantype)
#        return

#From logic/spectrum.py
    def save_spectrum_data(self, background=False, name_tag='', custom_header=None):
        """ Saves the current spectrum data to a file.

        @param bool background: Whether this is a background spectrum (dark field) or not.

        @param string name_tag: postfix name tag for saved filename.

        @param OrderedDict custom_header:
            This ordered dictionary is added to the default data file header. It allows arbitrary
            additional experimental information to be included in the saved data file header.
        """
        self.repetition_count += 1 #increase repetitioncount

        #filepath = self._save_logic.get_path_for_module(module_name='spectra')
        if background:
            filelabel = 'background'
            spectrum_data = self._spectrum_background
        else:
            filelabel = 'scan'
            spectrum_data = self.countdata #countdata_smoothed?

            # Don't include initialization 0's
            mask = (spectrum_data == 0).all(0)
            start_idx = np.argmax(~mask)
            spectrum_data = spectrum_data[:, start_idx:]
            self.countdata = spectrum_data #save over countdata to not include initialization 0s

        # Add name_tag as postfix to filename
        if name_tag != '':
            filelabel = filelabel + '_' + name_tag

        # write experimental parameters
        parameters = OrderedDict()
        parameters['Spectrometer acquisition repetitions'] = self.repetition_count

   #      # add all fit parameter to the saved data:
   #      if self.fc.current_fit_result is not None:
   #          parameters['Fit function'] = self.fc.current_fit
   #
   #          for name, param in self.fc.current_fit_param.items():
    #             parameters[name] = str(param)

        # add any custom header params
        if custom_header is not None:
            for key in custom_header:
                parameters[key] = custom_header[key]

        # prepare the data in an OrderedDict:
        data = OrderedDict()

        data['wavelength'] = spectrum_data[0, :]

        # # If the differential spectra arrays are not empty, save them as raw data
        # if len(self.diff_spec_data_mod_on) != 0 and len(self.diff_spec_data_mod_off) != 0:
        #     data['signal_mod_on'] = self.diff_spec_data_mod_on[1, :]
        #     data['signal_mod_off'] = self.diff_spec_data_mod_off[1, :]
        #     data['differential'] = spectrum_data[1, :]
        # else:
        data['signal'] = spectrum_data[1, :]
        #
        # if not background and len(self._spectrum_data_corrected) != 0:
        #     data['corrected'] = self._spectrum_data_corrected[1, :]

        fig = self.draw_figure()

        # Save to file
        self._save_logic.save_data(data,
                                   filepath=self.filepath,
                                   parameters=parameters,
                                   filelabel=filelabel,
                                   plotfig=fig)
        self.log.debug('Scan Counts saved to:\n{0}'.format(self.filepath))

    #from spectrum.py in logic
    def draw_figure(self):
        """ Draw the summary plot to save with the data.

        @return fig fig: a matplotlib figure object to be saved to file.
        """
        wavelength = self.countdata[0, :]
        spec_data = self.countdata[1, :]

        prefix = ['', 'k', 'M', 'G', 'T']
        prefix_index = 0
        rescale_factor = 1

        # Rescale spectrum data with SI prefix
        while np.max(spec_data) / rescale_factor > 1000:
            rescale_factor = rescale_factor * 1000
            prefix_index = prefix_index + 1

        intensity_prefix = prefix[prefix_index]

        # Prepare the figure to save as a "data thumbnail"
        plt.style.use(self._save_logic.mpl_qd_style)

        fig, ax1 = plt.subplots()

        ax1.plot(wavelength,
                 spec_data / rescale_factor,
                 linestyle=':',
                 linewidth=0.5
                 )

        # If there is a fit, plot it also
 #       if self.fc.current_fit_result is not None:
 #           ax1.plot(self.spectrum_fit[0] * 1e9,  # convert m to nm for plot
 #                    self.spectrum_fit[1] / rescale_factor,
 #                    marker='None'
 #                    )

        ax1.set_xlabel('Wavelength (nm)')
        ax1.set_ylabel('Intensity ({}count)'.format(intensity_prefix))

        fig.tight_layout()

        return fig

    def order_data(self):
        #put data in wavelength order
        temp = np.transpose(self.countdata)
        temp = temp[temp[:, 0].argsort()]
        self.countdata = np.transpose(temp)


    # FIXME: Not implemented for self._counting_mode == 'gated'
    def startCount(self):
        """ This is called externally, and is basically a wrapper that
            redirects to the chosen counting mode start function.

            @return error: 0 is OK, -1 is error
        """
        #First start the laser scan
        #   Send TCP message to M2 laser to start the terascan
        self._laser.setup_terascan(self.scanParams["scantype"], tuple([1E9*x for x in self.scanParams["scanbounds"]]),
                                   self.scanParams["scanrate"])
        self._laser.start_terascan(self.scanParams["scantype"])


        # Sanity checks
        constraints = self.get_hardware_constraints()
        # TODO: BUG FIXED HERE: introduce corresponding changes to GitHub files
        if self._counting_mode.value not in [constraints.counting_mode[j].value for j in range(len(constraints.counting_mode))]:
            self.log.error('Unknown counting mode "{0}". Cannot start the counter.'
                           ''.format(self._counting_mode))
            self.sigCountStatusChanged.emit(False)
            return -1
        # ORIGINAL VERSION
        # constraints = self.get_hardware_constraints()
        # if self._counting_mode not in constraints.counting_mode:
        #     self.log.error('Unknown counting mode "{0}". Cannot start the counter.'
        #                    ''.format(self._counting_mode))
        #     self.sigCountStatusChanged.emit(False)
        #     return -1

        with self.threadlock:
            # Lock module
            if self.module_state() != 'locked':
                self.module_state.lock()
            else:
                self.log.warning('Counter already running. Method call ignored.')
                return 0

            # Set up clock
            clock_status = self._counting_device.set_up_clock(clock_frequency=self._count_frequency)
            if clock_status < 0:
                self.module_state.unlock()
                self.sigCountStatusChanged.emit(False)
                return -1

            # Set up counter
            if self._counting_mode == CountingMode['FINITE_GATED']:
                counter_status = self._counting_device.set_up_counter(counter_buffer=self._count_length)
            # elif self._counting_mode == CountingMode['GATED']:
            #
            else:
                counter_status = self._counting_device.set_up_counter()
            if counter_status < 0:
                self._counting_device.close_clock()
                self.module_state.unlock()
                self.sigCountStatusChanged.emit(False)
                return -1

            # initialising the data arrays
            self.rawdata = np.zeros([len(self.get_channels())+1, self._counting_samples])
            self.countdata = np.zeros([len(self.get_channels())+1, self._count_length])
            self.countdata_smoothed = np.zeros([len(self.get_channels())+1, self._count_length])
            self._sampling_data = np.empty([len(self.get_channels())+1, self._counting_samples])

            # the sample index for gated counting
            self._already_counted_samples = 0

            # Start data reader loop
            self.sigCountStatusChanged.emit(True)
            self.sigCountDataNext.emit()
            return

    def get_fit_functions(self):
        """ Return the names of all ocnfigured fit functions.
        @return list(str): list of fit function names
        """
        return self.fc.fit_list.keys()

    def bin_data(self):

        #order data
        self.order_data()
        data = self.countdata

        #Don't include initialization 0's
        mask = (data==0).all(0)
        start_idx = np.argmax(~mask)
        data = data[:,start_idx:]

        x_wvln_min = np.min(data[0,:])
        x_wvln_max = np.max(data[0,:])

        #bin the data
        binnum = len(data[0,:]) - 1#not sure if this is best

        self.binneddata_x = np.linspace(x_wvln_min, x_wvln_max, binnum)
        self.binneddata_y = np.zeros(binnum)

        for n in range(0,len(data[0,:])): #i'm sure there is a less ugly way than this
            ct = data[1,n]
            arr = self.binneddata_x - data[0,n]
            if arr[arr > 0].size != 0: #not empty
                bn = np.where(arr == np.min(arr[arr > 0]))
            else:
                bn = binnum - 1

            self.binneddata_y[bn] = self.binneddata_y[bn] + ct




    def do_fit(self):
        """ Execute the currently configured fit
        """

        self.bin_data()

        self.wlog_fit_x, self.wlog_fit_y, result = self.fc.do_fit(
            self.binneddata_x,
            self.binneddata_y
            #self.histogram_axis, #does it need to be a regularly spaced linspace???
            #self.histogram   #where do uncertainties come in????? How can I implement y-axis uncertainty?
        )

        self.sig_fit_updated.emit()
        ######self.sig_data_updated.emit()