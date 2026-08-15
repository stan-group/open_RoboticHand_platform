

class HandPose:
	JOINT_NAMES = (
		"thumb_mcp",
		"thumb_abd",
		"thumb_pip",
		"thumb_dip",
		"index_abd",
		"index_mcp",
		"index_pip",
		"middle_abd",
		"middle_mcp",
		"middle_pip",
		"ring_abd",
		"ring_mcp",
		"ring_pip",
		"pinky_abd",
		"pinky_mcp",
		"pinky_pip",
		"wrist",
	)

	FINGER_JOINTS = {
		"index": ("index_abd", "index_mcp", "index_pip"),
		"middle": ("middle_abd", "middle_mcp", "middle_pip"),
		"ring": ("ring_abd", "ring_mcp", "ring_pip"),
		"pinky": ("pinky_abd", "pinky_mcp", "pinky_pip"),
	}

	def __init__(self, **angles: float) -> None:
		if not angles:
			raise ValueError("At least one joint angle must be provided.")

		self._angles = {joint_name: 0.0 for joint_name in self.JOINT_NAMES}
		unknown_joints = sorted(set(angles) - set(self.JOINT_NAMES))
		if unknown_joints:
			raise ValueError(f"Unknown joint names: {', '.join(unknown_joints)}")

		for joint_name, angle in angles.items():
			self._angles[joint_name] = float(angle)

	def __repr__(self):
		return f"HandPose({self._angles})"

	def dict(self):
		return dict(self._angles)

	def offset(
		self,
		finger: str,
		*,
		wrist: float = 0.0,
		abd: float = 0.0,
		mcp: float = 0.0,
		pip: float = 0.0,
	):
		if finger not in self.FINGER_JOINTS:
			raise ValueError(f"Unsupported finger {finger!r}; expected one of {', '.join(self.FINGER_JOINTS)}")

		offset_pose = self.dict()
		offset_pose["wrist"] += wrist
		abd_joint, mcp_joint, pip_joint = self.FINGER_JOINTS[finger]
		offset_pose[abd_joint] += abd
		offset_pose[mcp_joint] += mcp
		offset_pose[pip_joint] += pip
		return offset_pose