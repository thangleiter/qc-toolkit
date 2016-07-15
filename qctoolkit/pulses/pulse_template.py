"""This module defines the abstract PulseTemplate class which is the basis of any
pulse model in the qctoolkit.

Classes:
    - PulseTemplate: Represents the parametrized general structure of a pulse.
    - AtomicPulseTemplate: PulseTemplate that does imply any control flow disruptions and can be
        directly translated into a waveform.
"""
from abc import ABCMeta, abstractmethod, abstractproperty
from typing import Dict, Set, Optional

from qctoolkit.serialization import Serializable

from qctoolkit.pulses.parameters import ParameterDeclaration, Parameter
from qctoolkit.pulses.sequencing import SequencingElement, InstructionBlock

__all__ = ["PulseTemplate", "AtomicPulseTemplate"]


class PulseTemplate(Serializable, SequencingElement, metaclass=ABCMeta):
    """A PulseTemplate represents the parametrized general structure of a pulse.

    A PulseTemplate described a pulse in an abstract way: It defines the structure of a pulse
    but might leave some timings or voltage levels undefined, thus declaring parameters.
    This allows to reuse a PulseTemplate for several pulses which have the same overall structure
    and differ only in concrete values for the parameters.
    Obtaining an actual pulse which can be executed by specifying values for these parameters is
    called instantiation of the PulseTemplate and achieved by invoking the sequencing process.
    """

    def __init__(self, identifier: Optional[str]=None) -> None:
        super().__init__(identifier)

    @abstractproperty
    def parameter_names(self) -> Set[str]:
        """The set of names of parameters required to instantiate this PulseTemplate."""

    @abstractproperty
    def parameter_declarations(self) -> Set[ParameterDeclaration]:
        """The set of ParameterDeclaration objects detailing all parameters required to instantiate
        this PulseTemplate.
        """

    @abstractproperty
    def is_interruptable(self) -> bool:
        """True, if this execution of this PulseTemplate can be interrupted at certain points,
        i.e., a Sequencer can translate this PulseTemplate partially.
        """

    @abstractproperty
    def num_channels(self) -> int:
        """Returns the number of hardware output channels this PulseTemplate defines."""


class AtomicPulseTemplate(PulseTemplate):
    """A PulseTemplate that does not imply any control flow disruptions and can be directly
    translated into a waveform.

    Implies that no AtomicPulseTemplate object is interruptable.
    """

    def __init__(self, identifier: Optional[str]=None):
        super().__init__(identifier=identifier)

    def is_interruptable(self) -> bool:
        return False

    @abstractmethod
    def build_waveform(self, parameters: Dict[str, Parameter]) -> Optional['Waveform']:
        """Translate this AtomicPulseTemplate into a waveform according to the given parameteres.

        Args:
            parameters (Dict(str -> Parameter)): A mapping of parameter names to Parameter objects.
        Returns:
            Waveform object represented by this AtomicPulseTemplate object or None, if this object
                does not represent a valid waveform.
        """

    def build_sequence(self,
                       sequencer: 'Sequencer',
                       parameters: Dict[str, Parameter],
                       conditions: Dict[str, 'Condition'],
                       instruction_block: InstructionBlock) -> None:
        waveform = self.build_waveform(parameters)
        if waveform:
            instruction_block.add_instruction_exec(waveform)