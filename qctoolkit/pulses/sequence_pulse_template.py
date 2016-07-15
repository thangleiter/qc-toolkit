"""This module defines SequencePulseTemplate, a higher-order hierarchical pulse template that
combines several other PulseTemplate objects for sequential execution."""


from typing import Dict, List, Tuple, Set, Optional, Any, Iterable, Union

from qctoolkit.serialization import Serializer

from qctoolkit.pulses.pulse_template import PulseTemplate
from qctoolkit.pulses.parameters import ParameterDeclaration, Parameter, \
    ParameterNotProvidedException
from qctoolkit.pulses.sequencing import InstructionBlock, Sequencer
from qctoolkit.pulses.conditions import Condition
from qctoolkit.pulses.pulse_template_parameter_mapping import PulseTemplateParameterMapping, \
    MissingMappingException

__all__ = ["SequencePulseTemplate"]


class SequencePulseTemplate(PulseTemplate):
    """A sequence of different PulseTemplates.
    
    SequencePulseTemplate allows to group several
    PulseTemplates (subtemplates) into one larger sequence,
    i.e., when instantiating a pulse from a SequencePulseTemplate
    all pulses instantiated from the subtemplates are queued for
    execution right after one another.
    SequencePulseTemplate requires to specify a mapping of
    parameter declarations from its subtemplates to its own, enabling
    renaming and mathematical transformation of parameters.
    """

    # a subtemplate consists of a pulse template and mapping functions for its "internal" parameters
    Subtemplate = Tuple[PulseTemplate, Dict[str, str]]  # pylint: disable=invalid-name

    def __init__(self,
                 subtemplates: List[Subtemplate],
                 external_parameters: List[str], # pylint: disable=invalid-sequence-index
                 identifier: Optional[str]=None) -> None:
        """Create a new SequencePulseTemplate instance.

        Requires a (correctly ordered) list of subtemplates in the form
        (PulseTemplate, Dict(str -> str)) where the dictionary is a mapping between the external
        parameters exposed by this SequencePulseTemplate to the parameters declared by the
        subtemplates, specifying how the latter are derived from the former, i.e., the mapping is
        subtemplate_parameter_name -> mapping_expression (as str) where the free variables in the
        mapping_expression are parameters declared by this SequencePulseTemplate.

        The following requirements must be satisfied:
            - for each parameter declared by a subtemplate, a mapping expression must be provided
            - each free variable in a mapping expression must be declared as an external parameter
                of this SequencePulseTemplate

        Args:
            subtemplates (List(Subtemplate)): The list of subtemplates of this
                SequencePulseTemplate as tuples of the form (PulseTemplate, Dict(str -> str)).
            external_parameters (List(str)): A set of names for external parameters of this
                SequencePulseTemplate.
            identifier (str): A unique identifier for use in serialization. (optional)
        Raises:
            MissingMappingException, if a parameter of a subtemplate is not mapped to the external
                parameters of this SequencePulseTemplate.
            MissingParameterDeclarationException, if a parameter mapping requires a parameter
                that was not declared in the external parameters of this SequencePulseTemplate.
        """
        super().__init__(identifier)

        num_channels = 0
        if subtemplates:
            num_channels = subtemplates[0][0].num_channels

        self.__parameter_mapping = PulseTemplateParameterMapping(external_parameters)

        for template, mapping_functions in subtemplates:
            # Consistency checks
            if template.num_channels != num_channels:
                raise ValueError("Subtemplates have different number of channels!")

            for parameter, mapping_function in mapping_functions.items():
                self.__parameter_mapping.add(template, parameter, mapping_function)

            remaining = self.__parameter_mapping.get_remaining_mappings(template)
            if remaining:
                raise MissingMappingException(template,
                                              remaining.pop())

        self.__subtemplates = [template for (template, _) in subtemplates]
        self.__is_interruptable = True

    @property
    def parameter_names(self) -> Set[str]:
        return self.__parameter_mapping.external_parameters

    @property
    def parameter_declarations(self) -> Set[ParameterDeclaration]:
        # TODO: min, max, default values not mapped (required?)
        return {ParameterDeclaration(name) for name in self.parameter_names}

    @property
    def subtemplates(self) -> List[Subtemplate]:
        return [(template, self.__parameter_mapping.get_template_map(template))
                for template in self.__subtemplates]

    @property
    def is_interruptable(self) -> bool:
        return self.__is_interruptable
    
    @is_interruptable.setter
    def is_interruptable(self, new_value: bool) -> None:
        self.__is_interruptable = new_value

    @property
    def num_channels(self) -> int:
        return self.__subtemplates[0].num_channels

    def requires_stop(self,
                      parameters: Dict[str, Parameter],
                      conditions: Dict[str, 'Condition']) -> bool:
        return False

    def build_sequence(self,
                       sequencer: Sequencer,
                       parameters: Dict[str, Parameter],
                       conditions: Dict[str, Condition],
                       instruction_block: InstructionBlock) -> None:
        # todo: currently ignores is_interruptable

        # detect missing or unnecessary parameters
        missing = self.parameter_names - parameters.keys()
        if missing:
            raise ParameterNotProvidedException(missing.pop())

        # push subtemplates to sequencing stack with mapped parameters
        for template in reversed(self.__subtemplates):
            inner_parameters = self.__parameter_mapping.map_parameters(template, parameters)
            sequencer.push(template, inner_parameters, conditions, instruction_block)

    def get_serialization_data(self, serializer: Serializer) -> Dict[str, Any]:
        data = dict()
        data['external_parameters'] = sorted(list(self.parameter_names))
        data['is_interruptable'] = self.is_interruptable

        subtemplates = []
        for subtemplate in self.__subtemplates:
            mapping_functions = self.__parameter_mapping.get_template_map(subtemplate)
            mapping_functions_strings = \
                {k: serializer.dictify(m) for k, m in mapping_functions.items()}
            subtemplate = serializer.dictify(subtemplate)
            subtemplates.append(dict(template=subtemplate, mappings=mapping_functions_strings))
        data['subtemplates'] = subtemplates

        data['type'] = serializer.get_type_identifier(self)
        return data

    @staticmethod
    def deserialize(serializer: Serializer,
                    is_interruptable: bool,
                    subtemplates: Iterable[Dict[str, Union[str, Dict[str, Any]]]],
                    external_parameters: Iterable[str],
                    identifier: Optional[str]=None) -> 'SequencePulseTemplate':
        subtemplates = \
            [(serializer.deserialize(d['template']),
             {k: str(serializer.deserialize(m))
              for k, m in d['mappings'].items()})
             for d in subtemplates]

        template = SequencePulseTemplate(subtemplates, external_parameters, identifier=identifier)
        template.is_interruptable = is_interruptable
        return template
