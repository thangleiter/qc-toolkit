import copy
import logging
import numbers

import numpy as np
from typing import Union, Dict, List, Set, Optional, NamedTuple, Any, Iterable

"""RELATED THIRD PARTY IMPORTS"""

"""LOCAL IMPORTS"""
from qctoolkit.serialization import Serializer

from .parameters import ParameterDeclaration, Parameter
from .pulse_template import PulseTemplate
from .sequencing import InstructionBlock, Sequencer
from .interpolation import InterpolationStrategy, LinearInterpolationStrategy, HoldInterpolationStrategy, JumpInterpolationStrategy
from .instructions import Waveform, WaveformTable
from .conditions import Condition
from .measurements import Measurement

logger = logging.getLogger(__name__)


__all__ = ["TablePulseTemplate"]


TableValue = Union[float, ParameterDeclaration]
TableEntry = NamedTuple("TableEntry", [('t', TableValue), ('v', TableValue), ('interp', InterpolationStrategy)])


class TablePulseTemplate(PulseTemplate):
    """Defines a pulse via linear interpolation of a sequence of (time,voltage)-pairs.
    
    TablePulseTemplate stores a list of (time,voltage)-pairs (the table) which is sorted
    by time and uniquely define a pulse structure via interpolation of voltages of subsequent
    table entries.
    TablePulseTemplate provides methods to declare parameters which may be referred to instead of
    using concrete values for both, time and voltage. If the time of a table entry is a parameter
    reference, it is sorted into the table according to the first value of default, minimum or maximum
    which is defined (not None) in the corresponding ParameterDeclaration. If none of these are defined,
    the entry is placed at the end of the table.
    A TablePulseTemplate may be flagged as representing a measurement pulse, meaning that it defines a
    measurement window.
    """

    def __init__(self, measurement=False, identifier: Optional[str]=None) -> None:
        super().__init__(identifier)
        self.__identifier = identifier
        self.__entries = [] # type: List[TableEntry]
        self.__time_parameter_declarations = {} # type: Dict[str, ParameterDeclaration]
        self.__voltage_parameter_declarations = {} # type: Dict[str, ParameterDeclaration]
        self.__is_measurement_pulse = measurement # type: bool
        self.__interpolation_strategies = {'linear': LinearInterpolationStrategy(),
                                           'hold': HoldInterpolationStrategy(), 
                                           'jump': JumpInterpolationStrategy()
                                          }
        self.__measurement = Measurement(self)

    @staticmethod
    def from_array(times: np.ndarray, voltages: np.ndarray, measurement=False) -> 'TablePulseTemplate':
        """Static constructor to build a TablePulse from numpy arrays.

        Args:
            times: 1D numpy array with time values
            voltages: 1D numpy array with voltage values

        Returns:
            TablePulseTemplate with the given values, hold interpolation everywhere and no free parameters.
        """
        res = TablePulseTemplate(measurement=measurement)
        for t, v in zip(times, voltages):
            res.add_entry(t, v, interpolation='hold')
        return res

    def add_entry(self,
                  time: Union[float, str, ParameterDeclaration], 
                  voltage: Union[float, str, ParameterDeclaration], 
                  interpolation: str = 'hold') -> None:
        """Add an entry to this TablePulseTemplate.
        
        The arguments time and voltage may either be real numbers or a string which
        references a parameter declaration by name or a ParameterDeclaration object.
        The following constraints hold:
        - If a non-existing parameter declaration is referenced (via string), it is created without min, max and default values.
        - Parameter declarations for the time domain may not be used multiple times. Else a ValueError is thrown.
        - ParameterDeclaration objects for the time domain may not refer to other ParameterDeclaration objects as min or max values. Else a ValueError is thrown.
        - If a ParameterDeclaration is provided, its min and max values will be set to its neighboring values if they were not set previously or would exceed neighboring bounds.
        - Each entries time value must be greater than its predecessor's, i.e.,
            - if the time value is a float and the previous time value is a float, the new value must be greater
            - if the time value is a float and the previous time value is a parameter declaration, the new value must not be smaller than the maximum of the parameter declaration
            - if the time value is a parameter declaration and the previous time value is a float, the new values minimum must be no smaller
            - if the time value is a parameter declaration and the previous time value is a parameter declaration, the new minimum must not be smaller than the previous minimum
              and the previous maximum must not be greater than the new maximum
        """

        # Check if interpolation value is valid
        if interpolation not in self.__interpolation_strategies.keys():
            raise ValueError("Interpolation strategy not implemented. Allowed values: {}.".format(', '.join(self.__interpolation_strategies.keys())))
        else:
            interpolation = self.__interpolation_strategies[interpolation]

        # If this is the first entry, there are a number of cases we have to check
        if not self.__entries:
            # if the first entry has a time that is either > 0 or a parameter declaration, insert a start point (0, 0)
            if not isinstance(time, numbers.Real) or time > 0:
                #self.__entries.append(TableEntry(0, 0, self.__interpolation_strategies['hold'])) # interpolation strategy for first entry is disregarded, could be anything
                last_entry = TableEntry(0, 0, self.__interpolation_strategies['hold'])
            # ensure that the first entry is not negative
            elif isinstance(time, numbers.Real) and time < 0:
                raise ValueError("Time value must not be negative, was {}.".format(time))
            elif time == 0:
                last_entry = TableEntry(-1, 0, self.__interpolation_strategies['hold'])
        else:
            last_entry = self.__entries[-1]


        # Handle time parameter/value
        # first case: time is a real number
        if isinstance(time, numbers.Real):
            if isinstance(last_entry.t, ParameterDeclaration):
                # set maximum value of previous entry if not already set
                if last_entry.t.max_value == float('+inf'):
                    last_entry.t.max_value = time

                if time < last_entry.t.absolute_max_value:
                    raise ValueError("Argument time must be no smaller than previous time parameter declaration's" \
                                     " maximum value. Parameter '{0}', Maximum {1}, Provided: {2}."
                                     .format(last_entry.t.name, last_entry.t.absolute_max_value, time))

            # if time is a real number, ensure that is it not less than the previous entry
            elif time <= last_entry.t:
                raise ValueError("Argument time must be greater than previous time value {0}, was: {1}!".format(last_entry.t, time))

        # second case: time is a string -> Create a new ParameterDeclaration and continue third case
        elif isinstance(time, str):
            time = ParameterDeclaration(time)

        # third case: time is a ParameterDeclaration
        # if time is (now) a ParameterDeclaration, verify it, insert it and establish references/dependencies to previous entries if necessary
        if isinstance(time, ParameterDeclaration):
            if time.name in self.__voltage_parameter_declarations:
                raise ValueError("Cannot use already declared voltage parameter '{}' in time domain.".format(time.name))
            if time.name not in self.__time_parameter_declarations:
                if isinstance(time.min_value, ParameterDeclaration):
                    raise ValueError("A ParameterDeclaration for a time parameter may not have a minimum value reference" \
                                     " to another ParameterDeclaration object.")
                if isinstance(time.max_value, ParameterDeclaration):
                    raise ValueError("A ParameterDeclaration for a time parameter may not have a maximum value reference" \
                                     " to another ParameterDeclaration object.")

                # make a (shallow) copy of the ParameterDeclaration to ensure that it can't be changed from outside the Table
                time = ParameterDeclaration(time.name, min=time.min_value, max=time.max_value, default=time.default_value)
                # set minimum value if not previously set
                # if last_entry.t is a ParameterDeclaration, its max_value field will be set accordingly by the min_value setter,
                #  ensuring a correct boundary relationship between both declarations 
                if time.min_value == float('-inf'):
                    time.min_value = last_entry.t

                # Check dependencies between successive time parameters
                if isinstance(last_entry.t, ParameterDeclaration):
                    
                    if last_entry.t.max_value == float('inf'):
                        last_entry.t.max_value = time

                    if time.absolute_min_value < last_entry.t.absolute_min_value:
                        raise ValueError("Argument time's minimum value must be no smaller than the previous time" \
                                         "parameter declaration's minimum value. Parameter '{0}', Minimum {1}, Provided {2}."
                                         .format(last_entry.t.name, last_entry.t.absolute_min_value, time.min_value))
                    if time.absolute_max_value < last_entry.t.absolute_max_value:
                        raise ValueError("Argument time's maximum value must be no smaller than the previous time" \
                                         " parameter declaration's maximum value. Parameter '{0}', Maximum {1}, Provided {2}."
                                         .format(last_entry.t.name, last_entry.t.absolute_max_value, time.max_value))
                else:
                    if time.min_value < last_entry.t:
                        raise ValueError("Argument time's minimum value {0} must be no smaller than the previous time value {1}."
                                         .format(time.min_value, last_entry.t))
            else:
                raise ValueError("A time parameter with the name {} already exists.".format(time.name))


        # Handle voltage parameter/value
        # construct a ParameterDeclaration if voltage is a parameter name string
        if isinstance(voltage, str):
            voltage = ParameterDeclaration(voltage)
            
        # if voltage is (now) a ParameterDeclaration, make use of it
        if isinstance(voltage, ParameterDeclaration):
            # check whether a ParameterDeclaration with the same name already exists and, if so, use that instead
            # such that the same object is used consistently for one declaration
            if voltage.name in self.__voltage_parameter_declarations:
                voltage = self.__voltage_parameter_declarations[voltage.name]
            elif (voltage.name in self.__time_parameter_declarations or
                        (isinstance(time, ParameterDeclaration) and voltage.name == time.name)):
                    raise ValueError("Argument voltage <{}> must not refer to a time parameter declaration.".format(voltage.name))
            
        # no special action if voltage is a real number

        # add declaration if necessary
        if isinstance(time, ParameterDeclaration):
            self.__time_parameter_declarations[time.name] = time
        if isinstance(voltage, ParameterDeclaration):
            self.__voltage_parameter_declarations[voltage.name] = voltage
        # in case we need a time 0 entry previous to the new entry
        if not self.__entries and (not isinstance(time, numbers.Real) or time > 0):
                self.__entries.append(last_entry)
        # finally, add the new entry to the table 
        self.__entries.append(TableEntry(time, voltage, interpolation))
        
    @property
    def entries(self) -> List[TableEntry]:
        """Return an immutable copies of this TablePulseTemplate's entries."""
        return copy.deepcopy(self.__entries)

    @property
    def parameter_names(self) -> Set[str]:
        """Return the set of names of declared parameters."""
        return set(self.__time_parameter_declarations.keys()) | set(self.__voltage_parameter_declarations.keys())

    @property
    def parameter_declarations(self) -> Set[ParameterDeclaration]:
        """Return a set of all parameter declaration objects of this TablePulseTemplate."""
        return set(self.__time_parameter_declarations.values()) | set(self.__voltage_parameter_declarations.values())
    
    @property
    def is_interruptable(self) -> bool:
        """Return true, if this PulseTemplate contains points at which it can halt if interrupted."""
        return False
        
    def get_entries_instantiated(self, parameters: Dict[str, Parameter]) -> List[TableEntry]:
        """Return a list of all table entries with concrete values provided by the given parameters.
        """
        instantiated_entries = [] # type: List[TableEntry]
        for entry in self.__entries:
            time_value = None # type: float
            voltage_value = None # type: float
            # resolve time parameter references
            if isinstance(entry.t, ParameterDeclaration):
                time_value = entry.t.get_value(parameters)
            else:
                time_value = entry.t
            # resolve voltage parameter references only if voltageParameters argument is not None, otherwise they are irrelevant
            if isinstance(entry.v, ParameterDeclaration):
                voltage_value= entry.v.get_value(parameters)
            else:
                voltage_value = entry.v
            
            instantiated_entries.append(TableEntry(time_value, voltage_value, entry.interp))
            
        # ensure that no time value occurs twice
        previous_time = -1
        for (time, _, _) in instantiated_entries:
            if time <= previous_time:
                raise Exception("Time value {0} is smaller than the previous value {1}.".format(time, previous_time))
            previous_time = time
            
        return TablePulseTemplate.__clean_entries(instantiated_entries)

    @staticmethod
    def __clean_entries(entries: List[TableEntry]) -> List[TableEntry]:
        """ Checks if three subsequent values have the same voltage value. If so, the intermediate is redundant and removed in-place."""
        if not entries:
            return entries

        length = len(entries)
        if length < 3: # for less than 3 points all are necessary
           return entries
        for index in range(length-2, 0, -1):
            previous_step = entries[index - 1]
            step = entries[index]
            next_step = entries[index + 1]
            if step.v == previous_step.v and step.v == next_step.v:
                entries.pop(index)
        return entries

    def build_sequence(self,
                       sequencer: Sequencer,
                       parameters: Dict[str, Parameter],
                       conditions: Dict[str, Condition],
                       instruction_block: InstructionBlock) -> None:
        instantiated = self.get_entries_instantiated(parameters)
        measurement = copy.deepcopy(self.__measurement)
        if instantiated:
            if self.__is_measurement_pulse:
                measurement.measure(instantiated[0].t, instantiated[-1].t)
            waveform = TableWaveform(tuple(instantiated), measurement.instantiate(parameters))
            instruction_block.add_instruction_exec(waveform)

    def requires_stop(self, parameters: Dict[str, Parameter], conditions: Dict[str, 'Condition']) -> bool:
        return any(parameters[name].requires_stop for name in parameters.keys() if (name in self.parameter_names) and not isinstance(parameters[name], numbers.Number))

    def get_serialization_data(self, serializer: Serializer) -> Dict[str, Any]:
        data = dict()
        data['is_measurement_pulse'] = self.__is_measurement_pulse
        data['time_parameter_declarations'] = [serializer._serialize_subpulse(self.__time_parameter_declarations[key]) for key in sorted(self.__time_parameter_declarations.keys())]
        data['voltage_parameter_declarations'] = [serializer._serialize_subpulse(self.__voltage_parameter_declarations[key]) for key in sorted(self.__voltage_parameter_declarations.keys())]
        entries = []
        for (time, voltage, interpolation) in self.__entries:
            if isinstance(time, ParameterDeclaration):
                time = time.name
            if isinstance(voltage, ParameterDeclaration):
                voltage = voltage.name
            entries.append((time, voltage, str(interpolation)))
        data['entries'] = entries
        data['type'] = serializer.get_type_identifier(self)
        return data

    @staticmethod
    def deserialize(serializer: Serializer,
                    time_parameter_declarations: Iterable[Any],
                    voltage_parameter_declarations: Iterable[Any],
                    entries: Iterable[Any],
                    is_measurement_pulse: bool,
                    identifier: Optional[str]=None) -> 'TablePulseTemplate':
        time_parameter_declarations = {declaration['name']: serializer.deserialize(declaration) for declaration in time_parameter_declarations}
        voltage_parameter_declarations = {declaration['name']: serializer.deserialize(declaration) for declaration in voltage_parameter_declarations}

        template = TablePulseTemplate(is_measurement_pulse, identifier=identifier)

        for (time, voltage, interpolation) in entries:
            if isinstance(time, str):
                time = time_parameter_declarations[time]
            if isinstance(voltage, str):
                voltage = voltage_parameter_declarations[voltage]
            template.add_entry(time, voltage, interpolation=interpolation)

        return template


class TableWaveform(Waveform):
    def __init__(self, waveform_table: WaveformTable, measurement: Measurement = None) -> None:
        if len(waveform_table) < 2:
            raise ValueError("The given WaveformTable has less than two entries.")
        super().__init__()
        self.__table = waveform_table
        self.__measurement = measurement

    @property
    def _compare_key(self) -> Any:
        return self.__table

    @property
    def duration(self) -> float:
        return self.__table[-1].t

    def sample(self, sample_times: np.ndarray, offset: float) -> np.ndarray:
        sample_times -= (sample_times[0] - offset)
        voltages = np.empty_like(sample_times)
        for entry1, entry2 in zip(self.__table[:-1], self.__table[1:]):  # iterate over interpolated areas
            indices = np.logical_and(sample_times >= entry1.t, sample_times <= entry2.t)
            voltages[indices] = entry2.interp((entry1.t, entry1.v), (entry2.t, entry2.v),
                                              sample_times[indices])  # evaluate interpolation at each time
        return voltages

    @property
    def measurement_windows(self, first_offset: float = 0):
        self.__measurement.offset = first_offset
        return self.__measurement.build()
