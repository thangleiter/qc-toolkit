%% Init
savePath = 'Y:\Common\GaAs\Triton 200\Backup\DATA\workspace';

%% Loading
if util.yes_no_input('Really load smdata?', 'n')
	load(fullfile(savePath, 'smdata_recent.mat'));
	fprintf('Loaded smdata\n');
end
load(fullfile(savePath, 'tunedata_recent.mat'));
load(fullfile(savePath, 'plsdata_recent.mat'));
global tunedata
global plsdata

%% Add virtual Alazar channel
% Idea: create a separate qctoolkit instrument
smdata.inst(sminstlookup('ATS9440Python')).channels(9,:) = 'ATSV';
smaddchannel(sminstlookup('ATS9440Python'), 9, smdata.inst(sminstlookup('ATS9440Python')).channels(9, :));
smdata.inst(sminstlookup('ATS9440Python')).data.virtual_channel = struct();
smdata.inst(sminstlookup('ATS9440Python')).datadim(9) = 0;


%%
smloadinst('dummy');
smaddchannel(sminstlookup('dummy'), 1, 'count');
smaddchannel(sminstlookup('dummy'), 2, 'time');
smdata.inst(8).datadim = [1; 1];
smset('count', 0)
smset('time', 0)

%% Setup plsdata from scratch
global plsdata
plsdata = struct( ...
	'path', 'Y:\Cerfontaine\Code\qc-tookit-pulses', ...
	'awg', struct('inst', [], 'hardwareSetup', [], 'sampleRate', 2e9, 'currentProgam', '', 'registeredPrograms', struct(), 'defaultChannelMapping', struct(), 'defaultWindowMapping', struct(), 'defaultParametersAndDicts', {{}}, 'defaultAddMarker', {{}}), ...
  'dict', struct('cache', [], 'path', 'Y:\Cerfontaine\Code\qctoolkit-dicts'), ...
	'qc', struct('figId', 801), ...
	'daq', struct('inst', [], 'defaultOperations', {{}}) ...
	);
plsdata.qc.backend = py.qctoolkit.serialization.FilesystemBackend(plsdata.path);
plsdata.qc.serializer = py.qctoolkit.serialization.Serializer(plsdata.qc.backend);

%% Alazar simulator
smdata.inst(sminstlookup('ATS9440Python')).data.address = 'simulator';
plsdata.daq.inst = py.qctoolkit.hardware.dacs.alazar.AlazarCard(...
	[]...
	);

%% Alazar
smopen('ATS9440Python');
% config = sm_setups.triton_200.AlazarDefaultSettings(); 
% smdata.inst(sminstlookup('ATS9440Python')).data.config = config;
dos('activate lab_master & python -i -c "import atsaverage.client; import atsaverage.gui; card = atsaverage.client.getNetworkCard(''ATS9440'', keyfile_or_key=b''ultra_save_default_key''); window = atsaverage.gui.ThreadedStatusWindow(card); window.start();" &', '-echo')

plsdata.daq.inst = py.qctoolkit.hardware.dacs.alazar.AlazarCard(...
	py.atsaverage.core.getLocalCard(1, 1)...
	);
% alazar = py.qctoolkit.hardware.dacs.alazar.AlazarCard(...
% 	smdata.inst(3).data.py.card ...
% 	);


%% Setup AWG
qc.setup_tabor_awg('realAWG', false, 'simulateAWG', true, 'taborDriverPath', 'Y:\Cerfontaine\Code\tabor');

%% Alazar
qc.setup_alazar_measurements('nQubits', 2, 'nMeasPerQubit', 2, 'disp', true);

%%
% Configure Alazar so the AWG uses the ATS 10MHz reference clock 
py.atsaverage.alazar.ConfigureAuxIO(plsdata.daq.inst.card.handle,...
	                                  py.getattr(py.atsaverage.alazar.AUX_IO_Mode, 'out_pacer'),...
																	  uint64(10));
																	
% Set Base Alazar Config
plsdata.daq.inst.config = py.atsaverage.config.ScanlineConfiguration.parse(sm_setups.triton_200.AlazarDefaultSettings());

%% Load and unload alazar api
% py.atsaverage.alazar.unload
% py.atsaverage.alazar.load('atsapi.dll')

%% AWG default settings
awgctrl('default');

%% Load example pulse (or execute qc-tookit-pulses\matlab\general_charge_scan.m)
charge_scan = qc.load_pulse('charge_scan');

%% Example parameters
parameters = struct( ...
	'N_y', 100, ...
	't_wait', 0, ... % ns
	'y_start', -1, ...
	'y_stop', 1, ...
	'x_stop', 1, ...
	'x_start', -1, ...
	'N_x', 100, ...
	't_meas', 192, ...
	'sample_rate', 2, ... % *1e9
	'W_fast', 1, ...
	'W_slow', 0, ...
	'X_fast', 0, ...
	'X_slow', 1, ...
	'Y_fast', 0, ...
	'Y_slow', 0, ...
	'Z_fast', 0, ...
	'Z_slow', 0, ...
	'meas_time_multiplier', int64(10), ...
	'rep_count', int64(5) ...
	);

%% Test many of the available Matlab commands
%%
parameters = qc.params_add_delim(parameters, 'charge_scan');

%%
parameters = qc.params_rm_delim(parameters);

%%
test_dict = qc.add_params_to_dict('chrg', parameters);
qc.save_dict(test_dict);

%%
clearvars test_dict
common_dict = qc.load_dict('test')

%%
instantiated_pulse = qc.instantiate_pulse(charge_scan, 'parameters', parameters);

%%
instantiated_pulse = qc.instantiate_pulse(instantiated_pulse);

%%
qc.plot_pulse(charge_scan, 'parameters', parameters)

%%
qc.plot_pulse(instantiated_pulse)

%%
qc.get_pulse_duration(charge_scan, parameters)

%%
qc.save_pulse(charge_scan, true);

%%
qc.get_pulse_params('charge_scan')

%%
qc.get_pulse_params(charge_scan)

%%
charge_scan = qc.load_pulse('charge_scan');

%%
test_dict = qc.add_params_to_dict('test', struct('x_start', 25, 'x_stop', 16), 'global');
qc.save_dict(test_dict);

%%
parameters2 = struct('x_start', nan, 'x_stop', nan);
parameters2 = qc.params_add_delim(parameters2, 'charge_scan')
qc.join_params_and_dicts(parameters2, 'test')

%%
common_dict = qc.load_dict('common');
qc.save_dict(common_dict);

%%
chrg_dict = qc.load_dict('chrg');
chrg_dict.charge_scan

%% THIS IS WORKING =)
% chrg_dict = qc.load_dict('chrg');
% chrg_dict.charge_scan.rep_count = 10;
% chrg_dict.charge_scan.meas_time_multiplier = 200;
% qc.save_dict(chrg_dict);
% p = chrg_dict.charge_scan;

p = struct( ...
	'N_y', 100, ...
	't_wait', 0, ... % ns
	'y_start', -1, ...
	'y_stop', 1, ...
	'x_stop', 1, ...
	'x_start', -1, ...
	'N_x', 100, ...
	't_meas', 192, ...
	'sample_rate', 2, ... % *1e9
	'W_fast', 1, ...
	'W_slow', 0, ...
	'X_fast', 0, ...
	'X_slow', 1, ...
	'Y_fast', 0, ...
	'Y_slow', 0, ...
	'Z_fast', 0, ...
	'Z_slow', 0, ...
	'meas_time_multiplier', int64(200), ...
	'rep_count', int64(5) ...
	);
pDelim = qc.params_add_delim(p, 'charge_scan');

scanQ1 = qc.conf_seq('program_name', 'charge_scan_q4', ...
	'pulse_template', 'charge_scan', ...
	'parameters_and_dicts', {pDelim}, ...
	'channel_mapping', struct('W', 'TABOR_A','X', 'TABOR_B','Y', 'TABOR_C', 'Z', 'TABOR_D', 'marker', 'TABOR_A_MARKER'), ...
	'window_mapping', struct('A', 'Qubit_1_Meas_1', 'B', 'Qubit_2_Meas_1'), ...
	'operations', {{'Downsample', 'Qubit1', 'Qubit_1_Meas_1_Mask_1'}, {'Downsample', 'Qubit2', 'Qubit_2_Meas_1_Mask_1'}}, ...
	'verbosity', 10, ...
	'nrep', 100, ...
	'disp_dim', 2, ...
	'force_update', false, ...
	'disp_ops', [3 4], ...
	'procfn_ops', {{@procfn_charge_scan, {p.N_x, p.N_y, p.rep_count}, [p.N_x, p.N_y]}, {@procfn_charge_scan, {p.N_x, p.N_y, p.rep_count}, [p.N_x, p.N_y]}} ...
	);
scanQ1.disp(1).xlabel = 'RFA';
scanQ1.disp(1).ylabel = 'RFB';
scanQ1.disp(2).xlabel = 'RFA';
scanQ1.disp(2).ylabel = 'RFB';

scanQ2 = qc.conf_seq('program_name', 'charge_scan_q2', ...
	'pulse_template', 'charge_scan', ...
	'parameters_and_dicts', {pDelim}, ...
	'channel_mapping', struct('Y', 'TABOR_A','Z', 'TABOR_B','W', 'TABOR_C', 'X', 'TABOR_D', 'marker', 'TABOR_A_MARKER'), ...
	'window_mapping', struct('A', 'Qubit_1_Meas_1', 'B', 'Qubit_2_Meas_1'), ...
	'operations', {{'Downsample', 'Qubit1', 'Qubit_1_Meas_1_Mask_1'}, {'Downsample', 'Qubit2', 'Qubit_2_Meas_1_Mask_1'}}, ...
	'verbosity', 10, ...
	'nrep', 100, ...
	'disp_dim', 2, ...
	'force_update', false, ...
	'disp_ops', [3 4], ...
	'procfn_ops', {{@procfn_charge_scan, {p.N_x, p.N_y, p.rep_count}, [p.N_x, p.N_y]}, {@procfn_charge_scan, {p.N_x, p.N_y, p.rep_count}, [p.N_x, p.N_y]}} ...
	);
scanQ2.disp(1).xlabel = 'RFC';
scanQ2.disp(1).ylabel = 'RFD';
scanQ2.disp(2).xlabel = 'RFC';
scanQ2.disp(2).ylabel = 'RFD';

%%
testData = smrun(scanQ1)

%%
testData = smrun(scanQ2)

