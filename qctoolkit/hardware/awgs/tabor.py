import fractions
import sys
import functools
import weakref
from typing import List, Tuple, Set, NamedTuple, Callable, Optional, Any, Sequence, cast, Generator
from enum import Enum
from collections import OrderedDict

# Provided by Tabor electronics for python 2.7
# a python 3 version is in a private repository on https://git.rwth-aachen.de/qutech
# Beware of the string encoding change!
import teawg
import numpy as np

from qctoolkit.utils.types import ChannelID
from qctoolkit.pulses.multi_channel_pulse_template import MultiChannelWaveform
from qctoolkit.hardware.program import Loop, make_compatible
from qctoolkit.hardware.util import voltage_to_uint16, make_combined_wave, find_positions
from qctoolkit.hardware.awgs.base import AWG


assert(sys.byteorder == 'little')


__all__ = ['TaborAWGRepresentation', 'TaborChannelPair']


class TaborSegment(tuple):
    """Represents one segment of two channels on the device. Convenience class."""
    def __new__(cls, ch_a: Optional[np.ndarray], ch_b: Optional[np.ndarray]):
        return tuple.__new__(cls, (ch_a, ch_b))

    def __init__(self, ch_a, ch_b):
        if ch_a is None and ch_b is None:
            raise TaborException('Empty TaborSegments are not allowed')
        if ch_a is not None and ch_b is not None and len(ch_a) != len(ch_b):
            raise TaborException('Channel entries to have to have the same length')

    def __hash__(self) -> int:
        return hash((bytes(self[0]) if self[0] is not None else 0,
                     bytes(self[1]) if self[1] is not None else 0))

    @property
    def num_points(self) -> int:
        return len(self[0]) if self[1] is None else len(self[1])

    def get_as_binary(self) -> np.ndarray:
        assert not (self[0] is None or self[1] is None)
        return make_combined_wave([self])


class TaborSequencing(Enum):
    SINGLE = 1
    ADVANCED = 2


class TaborProgram:
    def __init__(self,
                 program: Loop,
                 device_properties,
                 channels: Tuple[Optional[ChannelID], Optional[ChannelID]],
                 markers: Tuple[Optional[ChannelID], Optional[ChannelID]]):
        if len(channels) != device_properties['chan_per_part']:
            raise TaborException('TaborProgram only supports {} channels'.format(device_properties['chan_per_part']))
        if len(markers) != device_properties['chan_per_part']:
            raise TaborException('TaborProgram only supports {} markers'.format(device_properties['chan_per_part']))
        channel_set = frozenset(channel for channel in channels if channel is not None) | frozenset(marker
                                                                                                    for marker in
                                                                                                    markers if marker is not None)
        self._program = program

        self.__waveform_mode = None
        self._channels = tuple(channels)
        self._markers = tuple(markers)
        self.__used_channels = channel_set
        self.__device_properties = device_properties

        self._waveforms = []  # type: List[MultiChannelWaveform]
        self._sequencer_tables = []
        self._advanced_sequencer_table = []

        if self.program.repetition_count > 1:
            self.program.encapsulate()

        if self.program.depth() > 1:
            self.setup_advanced_sequence_mode()
            self.__waveform_mode = TaborSequencing.ADVANCED
        else:
            if self.program.depth() == 0:
                self.program.encapsulate()
            self.setup_single_sequence_mode()
            self.__waveform_mode = TaborSequencing.SINGLE

    @property
    def markers(self) -> Tuple[Optional[ChannelID], Optional[ChannelID]]:
        return self._markers

    @property
    def channels(self) -> Tuple[Optional[ChannelID], Optional[ChannelID]]:
        return self._channels

    def sampled_segments(self,
                         sample_rate: float,
                         voltage_amplitude: Tuple[float, float],
                         voltage_offset: Tuple[float, float],
                         voltage_transformation: Tuple[Callable, Callable]) -> Tuple[Sequence[TaborSegment],
                                                                                     Sequence[int]]:
        sample_rate = fractions.Fraction(sample_rate, 10**9)

        segment_lengths = [waveform.duration*sample_rate for waveform in self._waveforms]
        if not all(abs(int(segment_length) - segment_length) < 1e-10 and segment_length > 0
                   for segment_length in segment_lengths):
            raise TaborException('At least one waveform has a length that is no integer or smaller zero')
        segment_lengths = np.asarray(segment_lengths, dtype=np.uint64)

        if np.any(segment_lengths % 16 > 0) or np.any(segment_lengths < 192):
            raise TaborException('At least one waveform has a length that is smaller 192 or not a multiple of 16')
        sample_rate = float(sample_rate)
        time_array = np.arange(np.max(segment_lengths)) / sample_rate

        def voltage_to_data(waveform, time, channel):
            if self._channels[channel]:
                return voltage_to_uint16(
                    voltage_transformation[channel](
                        waveform.get_sampled(channel=self._channels[channel],
                                             sample_times=time)),
                    voltage_amplitude[channel],
                    voltage_offset[channel],
                    resolution=14)
            else:
                return np.full_like(time, 8192, dtype=np.uint16)

        def get_marker_data(waveform: MultiChannelWaveform, time):
            marker_data = np.zeros(len(time), dtype=np.uint16)
            for marker_index, markerID in enumerate(self._markers):
                if markerID is not None:
                    marker_data |= (waveform.get_sampled(channel=markerID, sample_times=time) != 0).\
                                       astype(dtype=np.uint16) << marker_index+14
            return marker_data

        segments = np.empty_like(self._waveforms, dtype=TaborSegment)
        for i, waveform in enumerate(self._waveforms):
            t = time_array[:int(waveform.duration*sample_rate)]
            segment_a = voltage_to_data(waveform, t, 0)
            segment_b = voltage_to_data(waveform, t, 1)
            assert (len(segment_a) == len(t))
            assert (len(segment_b) == len(t))
            seg_data = get_marker_data(waveform, t)
            segment_a |= seg_data
            segments[i] = TaborSegment(segment_a, segment_b)
        return segments, segment_lengths

    def setup_single_sequence_mode(self) -> None:
        assert self.program.depth() == 1

        sequencer_table = []
        waveforms = OrderedDict()

        for waveform, repetition_count in ((waveform_loop.waveform.get_subset_for_channels(self.__used_channels),
                                            waveform_loop.repetition_count)
                                           for waveform_loop in self.program):
            if waveform in waveforms:
                waveform_index = waveforms[waveform]
            else:
                waveform_index = len(waveforms)
                waveforms[waveform] = waveform_index
            sequencer_table.append((repetition_count, waveform_index, 0))

        self._waveforms = tuple(waveforms.keys())
        self._sequencer_tables = [sequencer_table]
        self._advanced_sequencer_table = [(self.program.repetition_count, 1, 0)]

    def setup_advanced_sequence_mode(self) -> None:
        assert self.program.depth() > 1
        assert self.program.repetition_count == 1

        self.program.flatten_and_balance(2)

        min_seq_len = self.__device_properties['min_seq_len']
        max_seq_len = self.__device_properties['max_seq_len']

        def check_merge_with_next(program, n):
            if (program[n].repetition_count == 1 and program[n+1].repetition_count == 1 and
                    len(program[n]) + len(program[n+1]) < max_seq_len):
                program[n][len(program[n]):] = program[n + 1][:]
                program[n + 1:n + 2] = []
                return True
            return False

        def check_partial_unroll(program, n):
            st = program[n]
            if sum(entry.repetition_count for entry in st) * st.repetition_count >= min_seq_len:
                if sum(entry.repetition_count for entry in st) < min_seq_len:
                    st.unroll_children()
                while len(st) < min_seq_len:
                    st.split_one_child()
                return True
            return False

        i = 0
        while i < len(self.program):
            self.program[i].assert_tree_integrity()
            if len(self.program[i]) > max_seq_len:
                raise TaborException('The algorithm is not smart enough to make sequence tables shorter')
            elif len(self.program[i]) < min_seq_len:
                assert self.program[i].repetition_count > 0
                if self.program[i].repetition_count == 1:
                    # check if merging with neighbour is possible
                    if i > 0 and check_merge_with_next(self.program, i-1):
                        pass
                    elif i+1 < len(self.program) and check_merge_with_next(self.program, i):
                        pass

                    # check if (partial) unrolling is possible
                    elif check_partial_unroll(self.program, i):
                        i += 1

                    # check if sequence table can be extended by unrolling a neighbor
                    elif (i > 0
                          and self.program[i - 1].repetition_count > 1
                          and len(self.program[i]) + len(self.program[i-1]) < max_seq_len):
                        self.program[i][:0] = self.program[i-1].copy_tree_structure()[:]
                        self.program[i - 1].repetition_count -= 1

                    elif (i+1 < len(self.program)
                          and self.program[i+1].repetition_count > 1
                          and len(self.program[i]) + len(self.program[i+1]) < max_seq_len):
                        self.program[i][len(self.program[i]):] = self.program[i+1].copy_tree_structure()[:]
                        self.program[i+1].repetition_count -= 1

                    else:
                        raise TaborException('The algorithm is not smart enough to make this sequence table longer')
                elif check_partial_unroll(self.program, i):
                    i += 1
                else:
                    raise TaborException('The algorithm is not smart enough to make this sequence table longer')
            else:
                i += 1

        for sequence_table in self.program:
            assert len(sequence_table) >= self.__device_properties['min_seq_len']
            assert len(sequence_table) <= self.__device_properties['max_seq_len']

        advanced_sequencer_table = []
        sequencer_tables = []
        waveforms = OrderedDict()
        for sequencer_table_loop in self.program:
            current_sequencer_table = []
            for waveform, repetition_count in ((waveform_loop.waveform.get_subset_for_channels(self.__used_channels),
                                                waveform_loop.repetition_count)
                                               for waveform_loop in sequencer_table_loop):
                if waveform in waveforms:
                    wf_index = waveforms[waveform]
                else:
                    wf_index = len(waveforms)
                    waveforms[waveform] = wf_index
                current_sequencer_table.append((repetition_count, wf_index, 0))

            if current_sequencer_table in sequencer_tables:
                sequence_no = sequencer_tables.index(current_sequencer_table) + 1
            else:
                sequence_no = len(sequencer_tables) + 1
                sequencer_tables.append(current_sequencer_table)

            advanced_sequencer_table.append((sequencer_table_loop.repetition_count, sequence_no, 0))

        self._advanced_sequencer_table = advanced_sequencer_table
        self._sequencer_tables = sequencer_tables
        self._waveforms = tuple(waveforms.keys())

    @property
    def program(self) -> Loop:
        return self._program

    def get_sequencer_tables(self) -> List[Tuple[int, int, int]]:
        return self._sequencer_tables

    def get_advanced_sequencer_table(self) -> List[Tuple[int, int, int]]:
        """Advanced sequencer table that can be used  via the download_adv_seq_table pytabor command"""
        return self._advanced_sequencer_table

    @property
    def waveform_mode(self) -> str:
        return self.__waveform_mode


class TaborAWGRepresentation:
    def __init__(self, instr_addr=None, paranoia_level=1, external_trigger=False, reset=False, mirror_addresses=()):
        """
        :param instr_addr:        Instrument address that is forwarded to teawag
        :param paranoia_level:    Paranoia level that is forwarded to teawg
        :param external_trigger:  Not supported yet
        :param reset:
        :param mirror_addresses:
        """
        self._instr = teawg.TEWXAwg(instr_addr, paranoia_level)
        self._mirrors = tuple(teawg.TEWXAwg(address, paranoia_level) for address in mirror_addresses)

        self._clock_marker = [0, 0, 0, 0]

        if external_trigger:
            raise NotImplementedError()  # pragma: no cover

        if reset:
            self.send_cmd(':RES')

        self.initialize()

        self._channel_pair_AB = TaborChannelPair(self, (1, 2), str(instr_addr) + '_AB')
        self._channel_pair_CD = TaborChannelPair(self, (3, 4), str(instr_addr) + '_CD')

    @property
    def channel_pair_AB(self) -> 'TaborChannelPair':
        return self._channel_pair_AB

    @property
    def channel_pair_CD(self) -> 'TaborChannelPair':
        return self._channel_pair_CD

    @property
    def main_instrument(self) -> teawg.TEWXAwg:
        return self._instr

    @property
    def mirrored_instruments(self) -> Sequence[teawg.TEWXAwg]:
        return self._mirrors

    @property
    def paranoia_level(self) -> int:
        return self._instr.paranoia_level

    @paranoia_level.setter
    def paranoia_level(self, val):
        for instr in self.all_devices:
            instr.paranoia_level = val

    @property
    def dev_properties(self) -> dict:
        return self._instr.dev_properties

    @property
    def all_devices(self) -> Sequence[teawg.TEWXAwg]:
        return (self._instr, ) + self._mirrors

    def send_cmd(self, cmd_str, paranoia_level=None):
        for instr in self.all_devices:
            instr.send_cmd(cmd_str=cmd_str, paranoia_level=paranoia_level)

    def send_query(self, query_str, query_mirrors=False) -> Any:
        if query_mirrors:
            return tuple(instr.send_query(query_str) for instr in self.all_devices)
        else:
            return self._instr.send_query(query_str)

    def send_binary_data(self, pref, bin_dat, paranoia_level=None):
        for instr in self.all_devices:
            instr.send_binary_data(pref, bin_dat=bin_dat, paranoia_level=paranoia_level)

    def download_segment_lengths(self, seg_len_list, pref=':SEGM:DATA', paranoia_level=None):
        for instr in self.all_devices:
            instr.download_segment_lengths(seg_len_list, pref=pref, paranoia_level=paranoia_level)

    def download_sequencer_table(self, seq_table, pref=':SEQ:DATA', paranoia_level=None):
        for instr in self.all_devices:
            instr.download_sequencer_table(seq_table, pref=pref, paranoia_level=paranoia_level)

    def download_adv_seq_table(self, seq_table, pref=':ASEQ:DATA', paranoia_level=None):
        for instr in self.all_devices:
            instr.download_adv_seq_table(seq_table, pref=pref, paranoia_level=paranoia_level)

    make_combined_wave = staticmethod(teawg.TEWXAwg.make_combined_wave)

    def _send_cmd(self, cmd_str, paranoia_level=None) -> Any:
        """Overwrite send_cmd for paranoia_level > 3"""
        if paranoia_level is None:
            paranoia_level = self.paranoia_level

        if paranoia_level < 3:
            super().send_cmd(cmd_str=cmd_str, paranoia_level=paranoia_level)  # pragma: no cover
        else:
            cmd_str = cmd_str.rstrip()

            if len(cmd_str) > 0:
                ask_str = cmd_str + '; *OPC?; :SYST:ERR?'
            else:
                ask_str = '*OPC?; :SYST:ERR?'

            *answers, opc, error_code_msg = self._visa_inst.ask(ask_str).split(';')

            error_code, error_msg = error_code_msg.split(',')
            error_code = int(error_code)
            if error_code != 0:
                _ = self._visa_inst.ask('*CLS; *OPC?')

                if error_code == -450:
                    # query queue overflow
                    self.send_cmd(cmd_str)
                else:
                    raise RuntimeError('Cannot execute command: {}\n{}: {}'.format(cmd_str, error_code, error_msg))

            assert len(answers) == 0

    @property
    def is_open(self) -> bool:
        return self._instr.visa_inst is not None  # pragma: no cover

    def select_channel(self, channel) -> None:
        if channel not in (1, 2, 3, 4):
            raise TaborException('Invalid channel: {}'.format(channel))

        self.send_cmd(':INST:SEL {channel}'.format(channel=channel))

    def select_marker(self, marker: int) -> None:
        """Select marker 1 or 2 of the currently active channel pair."""
        if marker not in (1, 2):
            raise TaborException('Invalid marker: {}'.format(marker))
        self.send_cmd(':SOUR:MARK:SEL {marker}'.format(marker=marker))

    def sample_rate(self, channel) -> int:
        if channel not in (1, 2, 3, 4):
            raise TaborException('Invalid channel: {}'.format(channel))
        return int(float(self.send_query(':INST:SEL {channel}; :FREQ:RAST?'.format(channel=channel))))

    def amplitude(self, channel) -> float:
        if channel not in (1, 2, 3, 4):
            raise TaborException('Invalid channel: {}'.format(channel))
        coupling = self.send_query(':INST:SEL {channel}; :OUTP:COUP?'.format(channel=channel))
        if coupling == 'DC':
            return float(self.send_query(':VOLT?'))
        elif coupling == 'HV':
            return float(self.send_query(':VOLT:HV?'))
        else:
            raise TaborException('Unknown coupling: {}'.format(coupling))

    def offset(self, channel: int) -> float:
        if channel not in (1, 2, 3, 4):
            raise TaborException('Invalid channel: {}'.format(channel))
        return float(self.send_query(':INST:SEL {channel}; :VOLT:OFFS?'.format(channel=channel)))

    def enable(self) -> None:
        self.send_cmd(':ENAB')

    def abort(self) -> None:
        self.send_cmd(':ABOR')

    def initialize(self) -> None:
        # 1. Select channel
        # 2. Turn off gated mode
        # 3. continous mode
        # 4. Armed mode (onlz generate waveforms after enab command)
        # 5. Expect enable signal from (USB / LAN / GPIB)
        # 6. Use arbitrary waveforms as marker source
        # 7. Expect jump command for sequencing from (USB / LAN / GPIB)
        setup_command = (
                    ":INIT:GATE OFF; :INIT:CONT ON; "
                    ":INIT:CONT:ENAB SELF; :INIT:CONT:ENAB:SOUR BUS; "
                    ":SOUR:MARK:SOUR USER; :SOUR:SEQ:JUMP:EVEN BUS ")
        self.send_cmd(':INST:SEL 1')
        self.send_cmd(setup_command)
        self.send_cmd(':INST:SEL 3')
        self.send_cmd(setup_command)


    def reset(self) -> None:
        self.send_cmd(':RES')
        self.initialize()
        self.channel_pair_AB.clear()
        self.channel_pair_CD.clear()

    def trigger(self) -> None:
        self.send_cmd(':TRIG')

    def get_readable_device(self, simulator=True) -> teawg.TEWXAwg:
        for device in self.all_devices:
            if device.fw_ver >= 3.0:
                if simulator:
                    if device.is_simulator:
                        return device
                else:
                    return device
        raise TaborException('No device capable of device data read')


TaborProgramMemory = NamedTuple('TaborProgramMemory', [('waveform_to_segment', np.ndarray),
                                                       ('program', TaborProgram)])


def with_configuration_guard(function_object: Callable[['TaborChannelPair', Any], Any]) -> Callable[['TaborChannelPair'],
                                                                                               Any]:
    """This decorator assures that the AWG is in configuration mode while the decorated method runs."""
    @functools.wraps(function_object)
    def guarding_method(channel_pair: 'TaborChannelPair', *args, **kwargs) -> Any:
        if channel_pair._configuration_guard_count == 0:
            channel_pair._enter_config_mode()
        channel_pair._configuration_guard_count += 1

        try:
            return function_object(channel_pair, *args, **kwargs)
        finally:
            channel_pair._configuration_guard_count -= 1
            if channel_pair._configuration_guard_count == 0:
                channel_pair._exit_config_mode()

    return guarding_method


def with_select(function_object: Callable[['TaborChannelPair', Any], Any]) -> Callable[['TaborChannelPair'], Any]:
    """Asserts the channel pair is selcted when the wrapped function is called"""
    @functools.wraps(function_object)
    def selector(channel_pair: 'TaborChannelPair', *args, **kwargs) -> Any:
        channel_pair.select()
        return function_object(channel_pair, *args, **kwargs)

    return selector


class PlottableProgram:
    TableEntry = NamedTuple('TableEntry', [('repetition_count', int),
                                           ('element_number', int),
                                           ('jump_flag', int)])

    def __init__(self, waveforms: List[np.ndarray],
                 sequence_tables: List[Tuple[np.ndarray, np.ndarray, np.ndarray]],
                 advanced_sequence_table: Tuple[np.ndarray, np.ndarray, np.ndarray]):

        self._waveforms = self._reformat_waveforms(waveforms)
        self._sequence_tables = [PlottableProgram._reformat_rep_seg_jump(sequence_table)
                                 for sequence_table in sequence_tables]
        self._advanced_sequence_table = PlottableProgram._reformat_rep_seg_jump(advanced_sequence_table)

    @staticmethod
    def _reformat_waveforms(waveforms: List[np.ndarray]) -> Tuple[Tuple[np.ndarray], Tuple[np.ndarray]]:
        """De-interleave the individual channels' waveform data"""
        return tuple(zip(*((waveform.reshape((-1, 16))[1::2, :].ravel(), waveform.reshape((-1, 16))[0::2, :].ravel())
                           for waveform in waveforms)))

    @classmethod
    def _reformat_rep_seg_jump(cls, rep_seg_jump_tuple: Tuple[np.ndarray, np.ndarray, np.ndarray]) -> List['PlottableProgram.TableEntry']:
        return list(cls.TableEntry(int(rep), int(seg_no), int(jump))
                    for rep, seg_no, jump in zip(*rep_seg_jump_tuple))

    def _get_advanced_sequence_table_without_idle(self) -> List['PlottableProgram.TableEntry']:
        if self._advanced_sequence_table[0] == (1, 1, 1):
            adv_seq_tab = self._advanced_sequence_table[1:]
        else:
            adv_seq_tab = self._advanced_sequence_table

        #  remove idle pulse at end
        while adv_seq_tab[-1] == (1, 1, 0):
            adv_seq_tab = adv_seq_tab[:-1]
        return adv_seq_tab

    def _iter_segment_table_entry(self) -> Generator[TableEntry, None, None]:
        for sequence_repeat, sequence_no, _ in self._get_advanced_sequence_table_without_idle():
            for _ in range(sequence_repeat):
                yield from self._sequence_tables[sequence_no - 1]

    def __iter__(self) -> Generator[np.ndarray, None, None]:
        for segment_repeat, segment_no, _ in self._iter_segment_table_entry():
            for _ in range(segment_repeat):
                yield self._waveforms[segment_no - 1]

    def get_waveforms(self, channel: int) -> List[np.ndarray]:
        return [self._waveforms[channel][segment_no - 1]
                for _, segment_no, _ in self._iter_segment_table_entry()]

    def get_repetitions(self) -> np.ndarray:
        return np.fromiter((segment_repeat
                            for segment_repeat, *_ in self._iter_segment_table_entry()), dtype=int)


class TaborChannelPair(AWG):
    def __init__(self, tabor_device: TaborAWGRepresentation, channels: Tuple[int, int], identifier: str):
        super().__init__(identifier)
        self._device =  weakref.ref(tabor_device)

        self._configuration_guard_count = 0
        self._is_in_config_mode = False

        if channels not in ((1, 2), (3, 4)):
            raise ValueError('Invalid channel pair: {}'.format(channels))
        self._channels = channels

        self._idle_segment = TaborSegment(voltage_to_uint16(voltage=np.zeros(192),
                                                    output_amplitude=0.5,
                                                    output_offset=0., resolution=14),
                                    voltage_to_uint16(voltage=np.zeros(192),
                                                    output_amplitude=0.5,
                                                    output_offset=0., resolution=14))
        self._idle_sequence_table = [(1, 1, 0), (1, 1, 0), (1, 1, 0)]

        self._known_programs = dict()  # type: Dict[str, TaborProgramMemory]
        self._current_program = None

        self._segment_lengths = None
        self._segment_capacity = None
        self._segment_hashes = None
        self._segment_references = None

        self._sequencer_tables = None
        self._advanced_sequence_table = None

        self.clear()

    def select(self) -> None:
        self.device.send_cmd(':INST:SEL {}'.format(self._channels[0]))

    @property
    def total_capacity(self) -> int:
        return int(self.device.dev_properties['max_arb_mem']) // 2

    @property
    def device(self) -> TaborAWGRepresentation:
        return self._device()

    def free_program(self, name: str) -> TaborProgramMemory:
        if name is None:
            raise TaborException('Removing "None" program is forbidden.')
        program = self._known_programs.pop(name)
        self._segment_references[program.waveform_to_segment] -= 1
        if self._current_program == name:
            self.change_armed_program(None)
        return program

    def _restore_program(self, name: str, program: TaborProgram) -> None:
        if name in self._known_programs:
            raise ValueError('Program cannot be restored as it is already known.')
        self._segment_references[program.waveform_to_segment] += 1
        self._known_programs[name] = program

    @property
    def _segment_reserved(self) -> np.ndarray:
        return self._segment_references > 0

    @property
    def _free_points_in_total(self) -> int:
        return self.total_capacity - np.sum(self._segment_capacity[self._segment_reserved])

    @property
    def _free_points_at_end(self) -> int:
        reserved_index = np.flatnonzero(self._segment_reserved)
        if len(reserved_index):
            return self.total_capacity - np.sum(self._segment_capacity[:reserved_index[-1]])
        else:
            return self.total_capacity

    @with_select
    def read_waveforms(self) -> List[np.ndarray]:
        device = self.device.get_readable_device(simulator=True)

        old_segment = device.send_query(':TRAC:SEL?')
        waveforms = []
        uploaded_waveform_indices = np.flatnonzero(self._segment_references) + 1
        for segment in uploaded_waveform_indices:
            device.send_cmd(':TRAC:SEL {}'.format(segment))
            waveforms.append(device.read_act_seg_dat())
        device.send_cmd(':TRAC:SEL {}'.format(old_segment))
        return waveforms

    @with_select
    def read_sequence_tables(self) -> List[Tuple[np.ndarray, np.ndarray, np.ndarray]]:
        device = self.device.get_readable_device(simulator=True)

        old_sequence = device.send_query(':SEQ:SEL?')
        sequences = []
        uploaded_sequence_indices = np.arange(len(self._sequencer_tables)) + 1
        for sequence in uploaded_sequence_indices:
            device.send_cmd(':SEQ:SEL {}'.format(sequence))
            sequences.append(device.read_sequencer_table())
        device.send_cmd(':SEQ:SEL {}'.format(old_sequence))
        return sequences

    @with_select
    def read_advanced_sequencer_table(self) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
        return self.device.get_readable_device(simulator=True).read_adv_seq_table()

    def read_complete_program(self) -> PlottableProgram:
        return PlottableProgram(self.read_waveforms(), self.read_sequence_tables(), self.read_advanced_sequencer_table())

    @with_configuration_guard
    @with_select
    def upload(self, name: str,
               program: Loop,
               channels: Tuple[Optional[ChannelID], Optional[ChannelID]],
               markers: Tuple[Optional[ChannelID], Optional[ChannelID]],
               voltage_transformation: Tuple[Callable, Callable],
               force: bool=False) -> None:
        """Upload a program to the AWG.

        The policy is to prefer amending the unknown waveforms to overwriting old ones."""

        if len(channels) != self.num_channels:
            raise ValueError('Channel ID not specified')
        if len(markers) != self.num_markers:
            raise ValueError('Markers not specified')
        if len(voltage_transformation) != self.num_channels:
            raise ValueError('Wrong number of voltage transformations')

        # adjust program to fit criteria
        sample_rate = self.device.sample_rate(self._channels[0])
        make_compatible(program, minimal_waveform_length=192, waveform_quantum=16, sample_rate=sample_rate*1e-9)

        # helper to restore previous state if upload is impossible
        to_restore = None
        if name in self._known_programs:
            if force:
                # save old program to restore in on error
                to_restore = (self.free_program(name), self._current_program)
            else:
                raise ValueError('{} is already known on {}'.format(name, self.identifier))

        try:
            # parse to tabor program
            tabor_program = TaborProgram(program,
                                         channels=tuple(channels),
                                         markers=markers,
                                         device_properties=self.device.dev_properties)
            
            # They call the peak to peak range amplitude
            ranges = (self.device.amplitude(self._channels[0]),
                      self.device.amplitude(self._channels[1]))

            voltage_amplitudes = (ranges[0]/2, ranges[1]/2)
            voltage_offsets = (0, 0)
            segments, segment_lengths = tabor_program.sampled_segments(sample_rate=sample_rate,
                                                                       voltage_amplitude=voltage_amplitudes,
                                                                       voltage_offset=voltage_offsets,
                                                                       voltage_transformation=voltage_transformation)

            waveform_to_segment, to_amend, to_insert = self._find_place_for_segments_in_memory(segments,
                                                                                               segment_lengths)
        except:
            if to_restore:
                self._restore_program(name, to_restore[1])
                self._current_program = to_restore[0]
            raise

        self._segment_references[waveform_to_segment[waveform_to_segment >= 0]] += 1

        for wf_index in np.flatnonzero(to_insert > 0):
            segment_index = to_insert[wf_index]
            self._upload_segment(to_insert[wf_index], segments[wf_index])
            waveform_to_segment[wf_index] = segment_index

        if np.any(to_amend):
            segments_to_amend = segments[to_amend]
            waveform_to_segment[to_amend] = self._amend_segments(segments_to_amend)

        self._known_programs[name] = TaborProgramMemory(waveform_to_segment=waveform_to_segment,
                                                        program=tabor_program)

    @with_configuration_guard
    @with_select
    def clear(self) -> None:
        """Delete all segments and clear memory"""
        self.device.select_channel(self._channels[0])
        self.device.send_cmd(':TRAC:DEL:ALL')
        self.device.send_cmd(':SOUR:SEQ:DEL:ALL')
        self.device.send_cmd(':ASEQ:DEL')

        self.device.send_cmd(':TRAC:DEF 1, 192')
        self.device.send_cmd(':TRAC:SEL 1')
        self.device.send_cmd(':TRAC:MODE COMB')
        self.device.send_binary_data(pref=':TRAC:DATA', bin_dat=self._idle_segment.get_as_binary())

        self._segment_lengths = 192*np.ones(1, dtype=np.uint32)
        self._segment_capacity = 192*np.ones(1, dtype=np.uint32)
        self._segment_hashes = np.ones(1, dtype=np.int64) * hash(self._idle_segment)
        self._segment_references = np.ones(1, dtype=np.uint32)

        self._advanced_sequence_table = []
        self._sequencer_tables = []

        self._known_programs = dict()
        self.change_armed_program(None)

    def _find_place_for_segments_in_memory(self, segments: Sequence, segment_lengths: Sequence) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
        """
        1. Find known segments
        2. Find empty spaces with fitting length
        3. Find empty spaces with bigger length
        4. Amend remaining segments
        :param segments:
        :param segment_lengths:
        :return:
        """
        segment_hashes = np.fromiter((hash(segment) for segment in segments), count=len(segments), dtype=np.int64)

        waveform_to_segment = find_positions(self._segment_hashes, segment_hashes)

        # separate into known and unknown
        unknown = (waveform_to_segment == -1)
        known = ~unknown

        known_pos_in_memory = waveform_to_segment[known]

        assert len(known_pos_in_memory) == 0 or np.all(self._segment_hashes[known_pos_in_memory] == segment_hashes[known])

        new_reference_counter = self._segment_references.copy()
        new_reference_counter[known_pos_in_memory] += 1

        to_upload_size = np.sum(segment_lengths[unknown] + 16)
        free_points_in_total = self.total_capacity - np.sum(self._segment_capacity[self._segment_references > 0])
        if free_points_in_total < to_upload_size:
            raise MemoryError('Not enough free memory',
                              free_points_in_total,
                              to_upload_size,
                              self._free_points_in_total)

        to_amend = cast(np.ndarray, unknown)
        to_insert = np.full(len(segments), fill_value=-1, dtype=np.int64)

        reserved_indices = np.flatnonzero(new_reference_counter > 0)
        first_free = reserved_indices[-1] + 1 if len(reserved_indices) else 0

        free_segments = new_reference_counter[:first_free] == 0
        free_segment_count = np.sum(free_segments)

        # look for a free segment place with the same length
        for segment_idx in np.flatnonzero(to_amend):
            if free_segment_count == 0:
                break

            pos_of_same_length = np.logical_and(free_segments, segment_lengths[segment_idx] == self._segment_capacity[:first_free])
            idx_same_length = np.argmax(pos_of_same_length)
            if pos_of_same_length[idx_same_length]:
                free_segments[idx_same_length] = False
                free_segment_count -= 1

                to_amend[segment_idx] = False
                to_insert[segment_idx] = idx_same_length

        # try to find places that are larger than the segments to fit in starting with the large segments and large
        # free spaces
        segment_indices = np.flatnonzero(to_amend)[np.argsort(segment_lengths[to_amend])[::-1]]
        capacities = self._segment_capacity[:first_free]
        for segment_idx in segment_indices:
            free_capacities = capacities[free_segments]
            free_segments_indices = np.flatnonzero(free_segments)[np.argsort(free_capacities)[::-1]]

            if len(free_segments_indices) == 0:
                break

            fitting_segment = np.argmax((free_capacities >= segment_lengths[segment_idx])[::-1])
            fitting_segment = free_segments_indices[fitting_segment]
            if self._segment_capacity[fitting_segment] >= segment_lengths[segment_idx]:
                free_segments[fitting_segment] = False
                to_amend[segment_idx] = False
                to_insert[segment_idx] = fitting_segment

        free_points_at_end = self.total_capacity - np.sum(self._segment_capacity[:first_free])
        if np.sum(segment_lengths[to_amend] + 16) > free_points_at_end:
            raise MemoryError('Fragmentation does not allow upload.',
                              np.sum(segment_lengths[to_amend] + 16),
                              free_points_at_end,
                              self._free_points_at_end)

        return waveform_to_segment, to_amend, to_insert

    @with_select
    @with_configuration_guard
    def _upload_segment(self, segment_index: int, segment: TaborSegment) -> None:
        if self._segment_references[segment_index] > 0:
            raise ValueError('Reference count not zero')
        if segment.num_points > self._segment_capacity[segment_index]:
            raise ValueError('Cannot upload segment here.')

        segment_no = segment_index + 1

        self.device.send_cmd(':TRAC:DEF {}, {}'.format(segment_no, segment.num_points))
        self._segment_lengths[segment_index] = segment.num_points

        self.device.send_cmd(':TRAC:SEL {}'.format(segment_no))

        self.device.send_cmd(':TRAC:MODE COMB')
        wf_data = segment.get_as_binary()

        self.device.send_binary_data(pref=':TRAC:DATA', bin_dat=wf_data)
        self._segment_references[segment_index] = 1
        self._segment_hashes[segment_index] = hash(segment)

    @with_select
    @with_configuration_guard
    def _amend_segments(self, segments: List[TaborSegment]) -> np.ndarray:
        new_lengths = np.asarray([s.num_points for s in segments], dtype=np.uint32)

        wf_data = make_combined_wave(segments)
        trac_len = len(wf_data) // 2

        segment_index = len(self._segment_capacity)
        first_segment_number = segment_index + 1
        self.device.send_cmd(':TRAC:DEF {},{}'.format(first_segment_number, trac_len))
        self.device.send_cmd(':TRAC:SEL {}'.format(first_segment_number))
        self.device.send_cmd(':TRAC:MODE COMB')
        self.device.send_binary_data(pref=':TRAC:DATA', bin_dat=wf_data)

        old_to_update = np.count_nonzero(self._segment_capacity != self._segment_lengths)
        segment_capacity = np.concatenate((self._segment_capacity, new_lengths))
        segment_lengths = np.concatenate((self._segment_lengths, new_lengths))
        segment_references = np.concatenate((self._segment_references, np.ones(len(segments), dtype=int)))
        segment_hashes = np.concatenate((self._segment_hashes, [hash(s) for s in segments]))
        if len(segments) < old_to_update:
            for i, segment in enumerate(segments):
                current_segment_number = first_segment_number + i
                self.device.send_cmd(':TRAC:DEF {},{}'.format(current_segment_number, segment.num_points))
        else:
            # flush the capacity
            self.device.download_segment_lengths(segment_capacity)

            # update non fitting lengths
            for i in np.flatnonzero(segment_capacity != segment_lengths):
                self.device.send_cmd(':TRAC:DEF {},{}'.format(i+1, segment_lengths[i]))

        self._segment_capacity = segment_capacity
        self._segment_lengths = segment_lengths
        self._segment_hashes = segment_hashes
        self._segment_references = segment_references

        return segment_index + np.arange(len(segments), dtype=np.int64)

    @with_select
    @with_configuration_guard
    def cleanup(self) -> None:
        """Discard all segments after the last which is still referenced"""
        reserved_indices = np.flatnonzero(self._segment_references > 0)
        old_end = len(self._segment_lengths)
        new_end = reserved_indices[-1]+1 if len(reserved_indices) else 0
        self._segment_lengths = self._segment_lengths[:new_end]
        self._segment_capacity = self._segment_capacity[:new_end]
        self._segment_hashes = self._segment_hashes[:new_end]
        self._segment_references = self._segment_references[:new_end]

        delete_cmd = ';'.join('TRAC:DEL {}'.format(i+1) for i in range(new_end, old_end))
        self.device.send_cmd(delete_cmd)

    def remove(self, name: str) -> None:
        """Remove a program from the AWG.

        Also discards all waveforms referenced only by the program identified by name.

        Args:
            name (str): The name of the program to remove.
        """
        self.free_program(name)
        self.cleanup()

    def set_marker_state(self, active) -> None:
        """Sets the marker state of this channel pair. According to the manual one connot turn them off/on seperatly."""
        command_string = ':INST:SEL {}; :SOUR:MARK:SEL 1; :SOUR:MARK:SOUR USER; :SOUR:MARK:STAT {}'.format(
            self._channels[0],
            'ON' if active else 'OFF')
        self.device.send_cmd(command_string)

    def set_channel_state(self, channel, active) -> None:
        command_string = ':INST:SEL {}; :OUTP {}'.format(self._channels[channel], 'ON' if active else 'OFF')
        self.device.send_cmd(command_string)

    @with_select
    def arm(self, name: str) -> None:
        if self._current_program == name:
            self.device.send_cmd('SEQ:SEL 1')
        else:
            self.change_armed_program(name)

    @with_select
    @with_configuration_guard
    def change_armed_program(self, name: Optional[str]) -> None:
        if name is None:
            sequencer_tables = [self._idle_sequence_table]
            advanced_sequencer_table = [(1, 1, 0)]
        else:
            waveform_to_segment_index, program = self._known_programs[name]
            waveform_to_segment_number = waveform_to_segment_index + 1

            # translate waveform number to actual segment
            sequencer_tables = [[(rep_count, waveform_to_segment_number[wf_index], jump_flag)
                                 for (rep_count, wf_index, jump_flag) in sequencer_table]
                                 for sequencer_table in program.get_sequencer_tables()]

            # insert idle sequence
            sequencer_tables = [self._idle_sequence_table] + sequencer_tables

            # adjust advanced sequence table entries by idle sequence table offset
            advanced_sequencer_table = [(rep_count, seq_no + 1, jump_flag)
                                        for rep_count, seq_no, jump_flag in program.get_advanced_sequencer_table()]

            if program.waveform_mode == TaborSequencing.SINGLE:
                assert len(advanced_sequencer_table) == 1
                assert len(sequencer_tables) == 2

                while len(sequencer_tables[1]) < self.device.dev_properties['min_seq_len']:
                    assert advanced_sequencer_table[0][0] == 1
                    sequencer_tables[1].append((1, 1, 0))

        # insert idle sequence in advanced sequence table
        advanced_sequencer_table = [(1, 1, 1)] + advanced_sequencer_table

        while len(advanced_sequencer_table) < self.device.dev_properties['min_aseq_len']:
            advanced_sequencer_table.append((1, 1, 0))

        # download all sequence tables
        for i, sequencer_table in enumerate(sequencer_tables):
            if i >= len(self._sequencer_tables) or self._sequencer_tables[i] != sequencer_table:
                self.device.send_cmd('SEQ:SEL {}'.format(i+1))
                self.device.download_sequencer_table(sequencer_table)
        self._sequencer_tables = sequencer_tables
        self.device.send_cmd('SEQ:SEL 1')

        self.device.download_adv_seq_table(advanced_sequencer_table)
        self._advanced_sequence_table = advanced_sequencer_table

        self._current_program = name

    @with_select
    def run_current_program(self) -> None:
        if self._current_program:
            self.device.send_cmd(':TRIG')
        else:
            raise RuntimeError('No program active')

    @property
    def programs(self) -> Set[str]:
        """The set of program names that can currently be executed on the hardware AWG."""
        return set(program.name for program in self._known_programs.keys())

    @property
    def sample_rate(self) -> float:
        return self.device.sample_rate(self._channels[0])

    @property
    def num_channels(self) -> int:
        return 2

    @property
    def num_markers(self) -> int:
        return 2

    def _enter_config_mode(self) -> None:
        """Enter the configuration mode if not already in. All outputs are set to the DC offset of the device and the sequencing is disabled
        as the manual states this speeds up sequence validation when uploading multiple sequences."""
        if self._is_in_config_mode is False:

            # 1. Selct channel pair
            # 2. Select DC as function shape
            # 3. Select build-in waveform mode

            if self.device.send_query(':INST:COUP:STAT?') == 'ON':
                self.device.send_cmd(':OUTP:ALL OFF')
            else:
                self.device.send_cmd(':INST:SEL {}; :OUTP OFF; :INST:SEL {}; :OUTP OFF'.format(*self._channels))
                
            self.set_marker_state(False)
            self.device.send_cmd(':SOUR:FUNC:MODE FIX')

            self._is_in_config_mode = True

    @with_select
    def _exit_config_mode(self) -> None:
        """Leave the configuration mode. Enter advanced sequence mode and turn on all outputs"""

        if self.device.send_query(':INST:COUP:STAT?') == 'ON':
            # Coupled -> switch all channels at once
            if self._channels == (1, 2):
                other_channel_pair = self.device.channel_pair_CD
            else:
                other_channel_pair = self.device.channel_pair_AB

            if not other_channel_pair._is_in_config_mode:
                self.device.send_cmd(':SOUR:FUNC:MODE ASEQ')
                self.device.send_cmd(':SEQ:SEL 1')
                self.device.send_cmd(':OUTP:ALL ON')

        else:
            self.device.send_cmd(':SOUR:FUNC:MODE ASEQ')
            self.device.send_cmd(':SEQ:SEL 1')

            self.device.send_cmd(':INST:SEL {}; :OUTP ON; :INST:SEL {}; :OUTP ON'.format(*self._channels))

        self.set_marker_state(True)
        self._is_in_config_mode = False


class TaborException(Exception):
    pass
