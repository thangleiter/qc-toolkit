"""This module defines the class MeasurementPulse."""

from typing import Optional, Set, Dict, List, Any

from qctoolkit.serialization import Serializer
from qctoolkit.pulses.instructions import Waveform
from qctoolkit.pulses.conditions import Condition
from qctoolkit.pulses.parameters import ParameterDeclaration, Parameter
from qctoolkit.pulses.pulse_template import AtomicPulseTemplate


__all__ = ['MeasurementPulseTemplate']


class MeasurementPulseTemplate(AtomicPulseTemplate):
    """Declares an AtomicPulseTemplate to be measurable.

    MeasurementPulseTemplate is a decorator that turn any AtomicPulseTemplate into a measureable one
    by adding information required for the measurement such as how the measured data should be
    treated (left as raw measurement input or processed, e.g., average, min/max).

    The properties of the inner AtomicPulseTemplate are left unchanged and simply pass through this
    decorator class.
    """

    def __init__(self,
                 inner_template: AtomicPulseTemplate,
                 measurement_type: str='raw',
                 identifier: Optional[str]=None) -> None:
        """Create a new MeasurementPulseTemplate instance.

        Args:
            inner_template (AtomicPulseTemplate): The AtomicPulseTemplate object to be made
                measurable.
            measurement_type (str): Defines the processing type of the measurement.
                (todo: available types?)
            identifier (str): A unique identifier for use in serialization. (optional)
        """
        super().__init__(identifier=identifier)
        self.__inner_template = inner_template
        self.__measurement_type = measurement_type

    @property
    def measurement_type(self) -> str:
        return self.__measurement_type

    @property
    def num_channels(self) -> int:
        return self.__inner_template.num_channels

    @property
    def parameter_names(self) -> Set[str]:
        return self.__inner_template.parameter_names

    @property
    def parameter_declarations(self) -> Set[ParameterDeclaration]:
        return self.__inner_template.parameter_declarations

    def build_waveform(self, parameters: Dict[str, Parameter]) -> Optional[Waveform]:
        return self.__inner_template.build_waveform(parameters)

    def requires_stop(self,
                      parameters: Dict[str, Parameter],
                      conditions: Dict[str, Condition]) -> bool:
        return self.__inner_template.requires_stop(parameters, conditions)

    def get_serialization_data(self, serializer: Serializer) -> Dict[str, Any]:
        raise NotImplementedError()

    @staticmethod
    def deserialize(serializer: Serializer, **kwargs) -> 'MeasurementPulseTemplate':
        raise NotImplementedError()