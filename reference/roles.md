# 方向词典 · 检索关键词 · 面试考点

用途：把用户说的方向翻译成**检索关键词**和**面试准备清单**。JD 里同一个方向的叫法差别很大，
检索时把中英文和缩写都试一遍。

## 1. VLA / 具身大模型（Embodied AI）

- **岗位常见名**：具身智能算法工程师、具身大模型算法、多模态大模型（机器人方向）、
  Embodied AI Researcher、Robot Foundation Model、VLA 算法工程师。
- **检索关键词**：`VLA` `视觉语言动作` `具身智能` `embodied` `机器人大模型` `模仿学习`
  `imitation learning` `diffusion policy` `遥操作数据采集`。
- **考点**：ACT / Diffusion Policy / RT-1 / RT-2 / OpenVLA / π0 的结构差异；动作表示
  （绝对位姿 vs 相对增量 vs joint space）；action chunking 与时序平滑；
  数据采集与遥操作（VR、动捕、UMI、主从臂），数据规模与配比；
  语言条件化与任务泛化；评测协议（成功率、真机 vs 仿真差距）；常见失败模式与兜底策略。
- **加分**：真机数据 pipeline、跨本体迁移、开源复现（OpenVLA/LeRobot/RoboTwin）。

## 2. 强化学习 RL / sim2real

- **岗位常见名**：强化学习算法工程师、运动控制（RL 方向）、腿足运动算法、仿真算法工程师。
- **检索关键词**：`强化学习` `RL` `sim2real` `域随机化` `Isaac Gym` `Isaac Lab` `MuJoCo`
  `legged` `locomotion` `人形机器人运动控制`。
- **考点**：PPO/SAC/DDPG 的取舍与实现细节；reward shaping 与课程学习；
  observation/action 设计、历史帧与状态估计；domain randomization 与 actuator network；
  teacher-student / 特权信息蒸馏；仿真到实机的差距来源（延迟、摩擦、电机模型、传感噪声）；
  并行环境训练与吞吐；奖励崩塌、抖动、越界的调试经验。
- **加分**：真机跑通过的 RL 策略、Isaac Lab/Genesis/MJX 大规模训练经验。

## 3. 运动控制 / 全身控制（WBC）/ 力控

- **岗位常见名**：运动控制算法工程师、控制算法工程师、全身控制、力控算法、
  机器人动力学与控制。
- **检索关键词**：`运动控制` `WBC` `whole body control` `MPC` `模型预测控制` `阻抗控制`
  `导纳控制` `力控` `动力学建模` `步态规划` `ZMP` `质心轨迹`。
- **考点**：刚体动力学与递归牛顿-欧拉、雅可比与奇异性；LQR / iLQR / MPC 建模与实时求解
  （OSQP、qpOASES、HPIPM）；WBC 的任务优先级与 QP 构造；阻抗/导纳控制与接触稳定性；
  ZMP/DCM/Capture Point 与落脚点规划；状态估计（IMU+腿式里程计、卡尔曼滤波）；
  实时性（1kHz 控制环、EtherCAT、实时内核）；C++ 与实时代码的工程约束。
- **加分**：实机调过参数、能讲清楚一次失稳的定位过程。

## 4. Manipulation / 抓取 / 整臂操作（WAM）

- **岗位常见名**：机械臂算法工程师、抓取算法、操作（manipulation）算法、装配力控算法。
- **检索关键词**：`机械臂` `manipulation` `抓取` `grasp` `GraspNet` `位姿估计` `装配`
  `轴孔装配` `双臂协同` `whole-arm manipulation` `柔性作业` `运动规划` `MoveIt` `OMPL`。
- **考点**：抓取位姿检测（GraspNet-1Billion、AnyGrasp）、6D 位姿估计；
  运动规划（RRT*/CHOMP/TrajOpt）与碰撞检测；手眼标定（eye-in-hand / eye-to-hand）；
  接触富集任务（插孔、开门、拧螺丝）中的力控策略与搜索策略；
  双臂/移动操作的协调与冗余度利用；夹爪与灵巧手差异、触觉传感。
- **加分**：真机成功率数据、在产线/家庭场景做过泛化验证。

## 5. 感知 / SLAM / 三维视觉

- **检索关键词**：`SLAM` `视觉惯性里程计` `VIO` `激光雷达建图` `点云` `三维重建`
  `NeRF` `3D Gaussian Splatting` `目标检测` `多传感器融合` `标定`。
- **考点**：前端特征与后端优化（g2o/Ceres/GTSAM）、回环检测；紧耦合 VIO 与外参标定；
  点云配准与语义分割；多传感器时间同步；在移动机器人上的建图/重定位失效场景。

## 6. 机器人系统 / 嵌入式 / 部署

- **检索关键词**：`ROS2` `实时系统` `驱动开发` `电机控制` `FOC` `EtherCAT` `CAN`
  `边缘部署` `TensorRT` `模型量化` `Jetson` `RK3588`。
- **考点**：ROS2 通信与 DDS 调优、生命周期节点；实时性与调度；电机驱动与总线；
  模型部署与推理加速、端侧算力预算；日志、回放与现场调试工具链。

## 岗位类型的差别（影响投递策略）

- **研究员 / Research Scientist**：看顶会一作（CoRL、RSS、ICRA、IROS、CVPR、NeurIPS、ICLR）、
  开源影响力，通常要求博士或极强论文记录。
- **算法工程师**：看真机落地、成功率与迭代效率，项目描述要有量化结果。
- **仿真 / 数据工程**：看 pipeline 工程能力与吞吐，容易被低估但是进具身团队的好入口。
- **实习 → 转正**：早期公司普遍走实习转正，实习期长度和转正比例要在面试里直接问清楚。

## 简历与项目讲述框架

问题 → 方案选型（为什么不是别的）→ 我负责的部分 → 量化结果（成功率/误差/周期/吞吐）→
失败与改进。每个项目准备一个 60 秒版本和一个 5 分钟版本；真机项目一定准备一段视频或数据截图。
