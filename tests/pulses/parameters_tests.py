import unittest
from typing import Union

from qctoolkit.expressions import Expression
from qctoolkit.pulses.parameters import ConstantParameter, MappedParameter, ParameterNotProvidedException,\
    ParameterConstraint, ParameterConstraintViolation

from tests.serialization_dummies import DummySerializer
from tests.pulses.sequencing_dummies import DummyParameter


class ParameterTest(unittest.TestCase):
    
    def test_float_conversion_method(self) -> None:
        parameter = DummyParameter()
        self.assertEqual(parameter.value, float(parameter))


class ConstantParameterTest(unittest.TestCase):
    def __test_valid_params(self, value: float) -> None:
        constant_parameter = ConstantParameter(value)
        self.assertEqual(value, constant_parameter.get_value())
        self.assertEqual(value, float(constant_parameter))
        
    def test_float_values(self) -> None:
        self.__test_valid_params(-0.3)
        self.__test_valid_params(0)
        self.__test_valid_params(0.3)

    def test_requires_stop(self) -> None:
        constant_parameter = ConstantParameter(0.3)
        self.assertFalse(constant_parameter.requires_stop)

    def test_repr(self) -> None:
        constant_parameter = ConstantParameter(0.2)
        self.assertEqual("<ConstantParameter 0.2>", repr(constant_parameter))

    def test_get_serialization_data(self) -> None:
        constant_parameter = ConstantParameter(-0.2)
        serializer = DummySerializer()
        data = constant_parameter.get_serialization_data(serializer)
        self.assertEqual(dict(type=serializer.get_type_identifier(constant_parameter), constant=-0.2), data)

    def test_deserialize(self) -> None:
        serializer = DummySerializer()
        constant_parameter = ConstantParameter.deserialize(serializer, constant=3.1)
        self.assertEqual(3.1, constant_parameter.get_value())
        self.assertIsNone(constant_parameter.identifier)


class MappedParameterTest(unittest.TestCase):

    def test_requires_stop_and_get_value(self) -> None:
        p = MappedParameter(Expression("foo + bar * hugo"))
        with self.assertRaises(ParameterNotProvidedException):
            p.requires_stop
        with self.assertRaises(ParameterNotProvidedException):
            p.get_value()

        foo = DummyParameter(-1.1)
        bar = DummyParameter(0.5)
        hugo = DummyParameter(5.2, requires_stop=True)
        ilse = DummyParameter(2356.4, requires_stop=True)

        p.dependencies = {'foo': foo, 'hugo': hugo, 'ilse': ilse}
        with self.assertRaises(ParameterNotProvidedException):
            p.requires_stop
        with self.assertRaises(ParameterNotProvidedException):
            p.get_value()

        p.dependencies = {'foo': foo, 'bar': bar, 'hugo': hugo}
        self.assertTrue(p.requires_stop)
        with self.assertRaises(Exception):
            p.get_value()

        hugo = DummyParameter(5.2, requires_stop=False)
        p.dependencies = {'foo': foo, 'bar': bar, 'hugo': hugo, 'ilse': ilse}
        self.assertFalse(p.requires_stop)
        self.assertEqual(1.5, p.get_value())

    def test_repr(self) -> None:
        p = MappedParameter(Expression("foo + bar * hugo"))
        self.assertIsInstance(repr(p), str)

    def test_get_serialization_data(self) -> None:
        exp = Expression("foo + bar * hugo")
        p = MappedParameter(exp)
        serializer = DummySerializer()
        data = p.get_serialization_data(serializer)
        self.assertEqual(dict(type=serializer.get_type_identifier(p), expression=str(id(exp))), data)

    def test_deserialize(self) -> None:
        serializer = DummySerializer()
        exp = Expression("foo + bar * hugo")
        serializer.subelements[id(exp)] = exp
        p = MappedParameter.deserialize(serializer, expression=id(exp))
        self.assertIsNone(p.identifier)


class ParameterConstraintTest(unittest.TestCase):
    def test_ordering(self):
        constraint = ParameterConstraint('a <= b')
        self.assertEqual(constraint.affected_parameters, {'a', 'b'})

        self.assertTrue(constraint.is_fulfilled(dict(a=1, b=2)))
        self.assertTrue(constraint.is_fulfilled(dict(a=2, b=2)))
        self.assertFalse(constraint.is_fulfilled(dict(a=2, b=1)))

    def test_equal(self):
        constraint = ParameterConstraint('a==b')
        self.assertEqual(constraint.affected_parameters, {'a', 'b'})

        self.assertTrue(constraint.is_fulfilled(dict(a=2, b=2)))
        self.assertFalse(constraint.is_fulfilled(dict(a=3, b=2)))

    def test_expressions(self):
        constraint = ParameterConstraint('Max(a, b) < a*c')
        self.assertEqual(constraint.affected_parameters, {'a', 'b', 'c'})

        self.assertTrue(constraint.is_fulfilled(dict(a=2, b=2, c=3)))
        self.assertFalse(constraint.is_fulfilled(dict(a=3, b=5, c=1)))

    def test_no_relation(self):
        with self.assertRaises(ValueError):
            ParameterConstraint('a*b')
        ParameterConstraint('1 < 2')


class ParameterNotProvidedExceptionTests(unittest.TestCase):

    def test(self) -> None:
        exc = ParameterNotProvidedException('foo')
        self.assertEqual("No value was provided for parameter 'foo' and no default value was specified.", str(exc))

        
if __name__ == "__main__":
    unittest.main(verbosity=2)

