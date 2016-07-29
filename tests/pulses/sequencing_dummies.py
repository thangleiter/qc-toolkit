"""STANDARD LIBRARY IMPORTS"""
from typing import Tuple, List, Dict, Optional, Set, Any

import numpy

"""LOCAL IMPORTS"""
from qctoolkit.serialization import Serializer
from qctoolkit.pulses.instructions import Waveform, Instruction, CJMPInstruction, GOTOInstruction, REPJInstruction
from qctoolkit.pulses.sequencing import Sequencer, InstructionBlock, SequencingElement
from qctoolkit.pulses.parameters import Parameter, ParameterDeclaration
from qctoolkit.pulses.pulse_template import AtomicPulseTemplate
from qctoolkit.pulses.interpolation import InterpolationStrategy
from qctoolkit.pulses.conditions import Condition


class DummyParameter(Parameter):

    def __init__(self, value: float = 0, requires_stop: bool = False) -> None:
        super().__init__()
        self.value = value
        self.requires_stop_ = requires_stop

    def get_value(self) -> float:
        return self.value

    @property
    def requires_stop(self) -> bool:
        return self.requires_stop_

    def get_serialization_data(self, serializer: Serializer) -> None:
            raise NotImplementedError()

    @staticmethod
    def deserialize(serializer: Serializer) -> 'DummyParameter':
        raise NotImplementedError()

class DummyNoValueParameter(Parameter):

    def __init__(self) -> None:
        super().__init__()

    def get_value(self) -> float:
        raise Exception("May not call get_value on DummyNoValueParameter.")

    @property
    def requires_stop(self) -> bool:
        return True

    def get_serialization_data(self, serializer: Serializer) -> None:
            raise NotImplementedError()

    @staticmethod
    def deserialize(serializer: Serializer) -> 'DummyParameter':
        raise NotImplementedError()

class DummySequencingElement(SequencingElement):

    def __init__(self, requires_stop: bool = False, push_elements: Tuple[InstructionBlock, List[SequencingElement]] = None) -> None:
        super().__init__()
        self.build_call_counter = 0
        self.requires_stop_call_counter = 0
        self.target_block = None
        self.parameters = None
        self.conditions = None
        self.requires_stop_ = requires_stop
        self.push_elements = push_elements
        self.parameter_names = set()
        self.condition_names = set()

    def build_sequence(self,
                       sequencer: Sequencer,
                       parameters: Dict[str, Parameter],
                       conditions: Dict[str, 'Condition'],
                       instruction_block: InstructionBlock) -> None:
        self.build_call_counter += 1
        self.target_block = instruction_block
        instruction_block.add_instruction(DummyInstruction(self))
        self.parameters = parameters
        self.conditions = conditions
        if self.push_elements is not None:
            for element in self.push_elements[1]:
                sequencer.push(element, parameters, conditions, self.push_elements[0])

    def requires_stop(self, parameters: Dict[str, Parameter], conditions: Dict[str, 'Conditions']) -> bool:
        self.requires_stop_call_counter += 1
        self.parameters = parameters
        self.conditions = conditions
        return self.requires_stop_


class DummyInstruction(Instruction):

    def __init__(self, elem: DummySequencingElement = None) -> None:
        super().__init__()
        self.elem = elem

    @property
    def compare_key(self) -> Any:
        return self.elem


class DummyInstructionBlock(InstructionBlock):

    def __init__(self) -> None:
        super().__init__()
        self.embedded_blocks = [] # type: Collection[InstructionBlock]

    def add_instruction(self, instruction: Instruction) -> None:
        super().add_instruction(instruction)
        if isinstance(instruction, (CJMPInstruction, GOTOInstruction, REPJInstruction)):
            self.embedded_blocks.append(instruction.target.block)


class DummyWaveform(Waveform):

    def __init__(self, duration: float=0, sample_output: numpy.ndarray=None, num_channels: int=1) -> None:
        super().__init__()
        self.duration_ = duration
        self.sample_output = sample_output
        self.num_channels_ = num_channels
        self.sample_calls = []

    @property
    def compare_key(self) -> Any:
        if self.sample_output is not None:
            return tuple(self.sample_output.tolist())
        else:
            return id(self)

    @property
    def duration(self) -> float:
        return self.duration_

    @property
    def num_channels(self) -> int:
        return self.num_channels_

    def sample(self, sample_times: numpy.ndarray, first_offset: float=0) -> numpy.ndarray:
        self.sample_calls.append((list(sample_times), first_offset))
        if self.sample_output is not None:
            return self.sample_output
        return sample_times


class DummySequencer(Sequencer):

    def __init__(self) -> None:
        super().__init__()
        self.sequencing_stacks = {} #type: Dict[InstructionBlock, List[StackElement]]

    def push(self,
             sequencing_element: SequencingElement,
             parameters: Dict[str, Parameter],
             conditions: Dict[str, 'Condition'],
             target_block: InstructionBlock = None) -> None:
        if target_block is None:
            target_block = self.__main_block

        if target_block not in self.sequencing_stacks:
            self.sequencing_stacks[target_block] = []

        self.sequencing_stacks[target_block].append((sequencing_element, parameters, conditions))

    def build(self) -> InstructionBlock:
        raise NotImplementedError()

    def has_finished(self):
        raise NotImplementedError()


class DummyInterpolationStrategy(InterpolationStrategy):

    def __init__(self) -> None:
        self.call_arguments = []

    def __call__(self, start: Tuple[float, float], end: Tuple[float, float], times: numpy.ndarray) -> numpy.ndarray:
        self.call_arguments.append((start, end, list(times)))
        return times

    def __repr__(self) -> str:
        return "DummyInterpolationStrategy {}".format(id(self))


class DummyCondition(Condition):

    def __init__(self, requires_stop: bool=False):
        super().__init__()
        self.requires_stop_ = requires_stop
        self.loop_call_data = {}
        self.branch_call_data = {}

    def requires_stop(self) -> bool:
        return self.requires_stop_

    def build_sequence_loop(self,
                            delegator: SequencingElement,
                            body: SequencingElement,
                            sequencer: Sequencer,
                            parameters: Dict[str, Parameter],
                            conditions: Dict[str, Condition],
                            instruction_block: InstructionBlock) -> None:
        self.loop_call_data = dict(
            delegator=delegator,
            body=body,
            sequencer=sequencer,
            parameters=parameters,
            conditions=conditions,
            instruction_block=instruction_block
        )

    def build_sequence_branch(self,
                              delegator: SequencingElement,
                              if_branch: SequencingElement,
                              else_branch: SequencingElement,
                              sequencer: Sequencer,
                              parameters: Dict[str, Parameter],
                              conditions: Dict[str, Condition],
                              instruction_block: InstructionBlock) -> None:
        self.branch_call_data = dict(
            delegator=delegator,
            if_branch=if_branch,
            else_branch=else_branch,
            sequencer=sequencer,
            parameters=parameters,
            conditions=conditions,
            instruction_block=instruction_block
        )


class DummyPulseTemplate(AtomicPulseTemplate):

    def __init__(self,
                 requires_stop: bool=False,
                 is_interruptable: bool=False,
                 parameter_names: Set[str]={},
                 num_channels: int=1,
                 duration: float=0,
                 waveform: Waveform=None) -> None:
        super().__init__()
        self.requires_stop_ = requires_stop
        self.is_interruptable_ = is_interruptable
        self.parameter_names_ = parameter_names
        self.build_sequence_calls = 0
        self.num_channels_ = num_channels
        self.duration = duration
        self.waveform = waveform
        self.build_waveform_calls = []

    @property
    def parameter_names(self) -> Set[str]:
        return set(self.parameter_names_)

    @property
    def parameter_declarations(self) -> Set[str]:
        return {ParameterDeclaration(name) for name in self.parameter_names}

    @property
    def is_interruptable(self) -> bool:
        return self.is_interruptable_

    @property
    def num_channels(self) -> int:
        return self.num_channels_

    def build_sequence(self,
                       sequencer: Sequencer,
                       parameters: Dict[str, Parameter],
                       conditions: Dict[str, Condition],
                       instruction_block: InstructionBlock):
        self.build_sequence_calls += 1

    def build_waveform(self, parameters: Dict[str, Parameter]) -> Optional[Waveform]:
        self.build_waveform_calls.append(parameters)
        if self.waveform is not None:
            return self.waveform
        return DummyWaveform(duration=self.duration, num_channels=self.num_channels)

    def requires_stop(self, parameters: Dict[str, Parameter], conditions: Dict[str, Condition]) -> bool:
        return self.requires_stop_

    def get_serialization_data(self, serializer: Serializer):
        raise NotImplementedError()

    @staticmethod
    def deserialize(serializer: Serializer,
                    condition: Dict[str, Any],
                    body: Dict[str, Any],
                    identifier: Optional[str]=None):
        raise NotImplementedError()
