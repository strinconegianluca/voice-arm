"""The simulated arm is a UR3e running the same URScript motions."""

from arm.ur3e import POSES, UR3e as SimulatedArm

__all__ = ["POSES", "SimulatedArm"]
