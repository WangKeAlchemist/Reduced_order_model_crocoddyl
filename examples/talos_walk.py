import time

import crocoddyl
import example_robot_data
import numpy as np
import pinocchio
import vhip


def buildSRBMFromRobot(robot_model):
    model = pinocchio.Model()
    placement = pinocchio.SE3.Identity()
    joint_com = model.addJoint(0, pinocchio.JointModelTranslation(), placement, 'com')  # for inverted pendulum model
    # joint_com = model.addJoint(0, pinocchio.JointModelFreeFlyer(), placement, 'com') # for single rigid body model
    body_inertia = pinocchio.Inertia.Zero()
    body_inertia.mass = pinocchio.computeTotalMass(robot_model.model)
    data = robot_model.data
    q0 = robot_model.q0
    com = robot_model.com(q0)
    body_placement = placement
    body_placement.translation[0] = com[0]
    body_placement.translation[1] = com[1]
    body_placement.translation[2] = com[2]
    model.appendBodyToJoint(joint_com, body_inertia, body_placement)
    return model


def generateVertexSet(foot_pos, foot_ori, foot_size):
    Vs = np.array([[1, 1, 1], [-1, 1, 1], [-1, -1, 1], [1, -1, 1]
                   ]) * foot_size / 2  # distance of 4 vertexes from the center of foot
    vertex_set = np.zeros((3, 8))
    lfoot_pos = foot_pos[:3]
    rfoot_pos = foot_pos[3:]
    R_l = foot_ori[:3, :]
    R_r = foot_ori[3:, :]
    vertex_set[:, 0] = lfoot_pos + R_l.dot(Vs[0, :])
    vertex_set[:, 1] = lfoot_pos + R_l.dot(Vs[1, :])
    vertex_set[:, 2] = lfoot_pos + R_l.dot(Vs[2, :])
    vertex_set[:, 3] = lfoot_pos + R_l.dot(Vs[3, :])
    vertex_set[:, 4] = rfoot_pos + R_r.dot(Vs[0, :])
    vertex_set[:, 5] = rfoot_pos + R_r.dot(Vs[1, :])
    vertex_set[:, 6] = rfoot_pos + R_r.dot(Vs[2, :])
    vertex_set[:, 7] = rfoot_pos + R_r.dot(Vs[3, :])
    return vertex_set


# add constraints for sum of weight
def createPhaseModel(robot_model,
                     cop,
                     foot_pos,
                     foot_ori,
                     foot_size=[0.2, 0.1, 0],
                     support=0,
                     xref=np.array([0.0, 0.0, 0.86, 0.1, 0.0, 0.0]),
                     mu=0.7,
                     Wx=np.array([0., 0., 10., 10., 10., 10.]),
                     Wu=np.array([50., 1., 1., 1., 1., 1., 1., 1.]),
                     wxreg=1,
                     wureg=1,
                     wutrack=50,
                     wxbox=1,
                     dt=4e-2):
    state = crocoddyl.StateVector(6)
    model = buildSRBMFromRobot(robot_model)
    multibody_state = crocoddyl.StateMultibody(model)
    runningCosts = crocoddyl.CostModelSum(state, 8)
    actuation = vhip.ActuationModelBipedContactVertex(state)
    actuation.set_reference(generateVertexSet(foot_pos, foot_ori, foot_size))
    if support == 1:  # Left support
        uRef = np.hstack([9.81 * pinocchio.computeTotalMass(robot_model.model), 0.25, 0.25, 0.25, 0.25, 0.0, 0.0, 0.0])
    elif support == 0:  # Double support
        uRef = np.hstack(
            [9.81 * pinocchio.computeTotalMass(robot_model.model), 0.125, 0.125, 0.125, 0.125, 0.125, 0.125, 0.125])
    elif support == -1:  # Right support
        uRef = np.hstack([9.81 * pinocchio.computeTotalMass(robot_model.model), 0.0, 0.0, 0.0, 0.0, 0.25, 0.25, 0.25])

    xRef = xref
    Mu = mu
    ub_x = np.hstack([cop, np.zeros(3)]) + np.array([0.3, 0.055, 0.95, 7., 7., 3])
    lb_x = np.hstack([cop, np.zeros(3)]) + np.array([-0.3, -0.055, 0.75, -7., -7., -3])
    # --------------- COM Constraints ------------------ #
    runningCosts.addCost(
        "comBox",
        crocoddyl.CostModelResidual(state,
                                    crocoddyl.ActivationModelQuadraticBarrier(crocoddyl.ActivationBounds(lb_x, ub_x)),
                                    crocoddyl.ResidualModelState(state, xRef, actuation.nu)), wxbox)
    # --------------- Track State Ref ------------------ #
    runningCosts.addCost(
        "comTrack",
        crocoddyl.CostModelResidual(state, crocoddyl.ActivationModelWeightedQuad(Wx),
                                    crocoddyl.ResidualModelState(state, xRef, actuation.nu)), wxreg)
    # --------------- Track Control Ref ------------------ #
    runningCosts.addCost("uTrack",
                         crocoddyl.CostModelResidual(state, crocoddyl.ActivationModelWeightedQuad(Wu),
                                                     crocoddyl.ResidualModelControl(state, uRef)), wutrack)  ## ||u||^2
    # --------------- Minimize Control ------------------ #
    runningCosts.addCost("uReg", crocoddyl.CostModelResidual(state,
                                                             crocoddyl.ResidualModelControl(state, actuation.nu)),
                         wureg)  ## ||u||^2
    # --------------- Control Residual Constraint ------------------ #
    lb_dr = np.array([0.])
    ub_dr = np.array([1.])
    runningCosts.addCost(
        "Control Bound Constraint",
        crocoddyl.CostModelResidual(
            state, crocoddyl.ActivationModelQuadraticBarrier(crocoddyl.ActivationBounds(lb_dr, ub_dr)),
            vhip.ControlBoundResidual(state, actuation.nu)), 1e2)

    # --------------- Asymmetric Friction Cone Constraint ------------------ #
    lb_rf = np.array([-np.inf, -np.inf, -np.inf, -np.inf])
    ub_rf = np.array([0., 0., 0., 0.])
    Afcr = vhip.AsymmetricFrictionConeResidual(state, 8, Mu, foot_ori)
    runningCosts.addCost(
        "Asymmetric Constraint",
        crocoddyl.CostModelResidual(
            state, crocoddyl.ActivationModelQuadraticBarrier(crocoddyl.ActivationBounds(lb_rf, ub_rf)), Afcr), 5)

    model = vhip.DifferentialActionModelVariableHeightPendulum(multibody_state, runningCosts, actuation)
    model.get_support_index(support)
    return crocoddyl.IntegratedActionModelEuler(model, dt)


def createTerminalModel(robot_model, cop, foot_pos, foot_ori, xref):
    return createPhaseModel(robot_model,
                            cop,
                            foot_pos,
                            foot_ori,
                            xref=xref,
                            Wx=np.array([50., 10., 50., 10., 10., 50.]),
                            wxreg=1e6,
                            dt=0.)


if __name__ == "__main__":
    foot_holds = np.array([[0.0, 0.0, 0.0], [0.0, -0.085, 0.05], [0.05, 0.0, 0.05], [0.1, 0.085, 0.1],
                           [0.1, 0.0, 0.1]])  # footsteps given
    phase = np.array([0, 1, 0, -1, 0])  # 0: double, 1: left, -1: right
    len_steps = phase.shape[0]
    robot_model = example_robot_data.load("talos")
    com_height = 0.86
    num_nodes_single_support = 20
    num_nodes_double_support = 10
    dt = 6e-2
    ################ Foot Placement #############################################
    foot_placements = np.zeros((len_steps, 6))  # :3, left support. 3:, right support
    foot_orientations = np.zeros((len_steps, 6))
    foot_orientations[1:, :3] = np.array([np.deg2rad(15), 0, 0])
    foot_orientations[1:, 3:] = np.array([np.deg2rad(-15), 0, 0])
    foot_orientations_rot_matrix = np.zeros((len_steps, 6, 3))
    support_indexes = phase
    support_durations = np.zeros(len_steps)
    ######### Should not do this, directly generate footplacements instead generating from CoP #################
    for i in range(len_steps):
        # generate rotation matrix from rpy: [R_l;R_r]
        foot_orientations_rot_matrix[i, :3, :] = pinocchio.utils.rpyToMatrix(foot_orientations[i, 0],
                                                                             foot_orientations[i, 1],
                                                                             foot_orientations[i, 2])
        foot_orientations_rot_matrix[i, 3:, :] = pinocchio.utils.rpyToMatrix(foot_orientations[i, 3],
                                                                             foot_orientations[i, 4],
                                                                             foot_orientations[i, 5])
        if phase[i] == 0:
            if i == 0 or i == len_steps - 1:
                foot_placements[i, 0] = foot_holds[i, 0]
                foot_placements[i, 2] = foot_holds[i, 2]
                foot_placements[i, 3] = foot_holds[i, 0]
                foot_placements[i, 5] = foot_holds[i, 2]
                foot_placements[i, 1] = foot_holds[i, 1] + 0.085
                foot_placements[i, 4] = foot_holds[i, 1] - 0.085
            else:
                foot_placements[i, :] = foot_placements[i - 1, :]
        elif phase[i] == -1:  # right support
            foot_placements[i, :3] = foot_holds[i + 1, :]
            foot_placements[i, 1] = 0.085
            foot_placements[i, 3:] = foot_placements[i - 1, 3:]
        elif phase[i] == 1:  # left support
            foot_placements[i, 3:] = foot_holds[i + 2, :]
            foot_placements[i, :3] = foot_placements[i - 1, :3]
            foot_placements[i, 4] = -0.085

        if support_indexes[i] == 0:
            support_durations[i] = num_nodes_double_support * dt
        else:
            support_durations[i] = num_nodes_single_support * dt

    print('Foot placements are ', foot_placements)
    locoModel = [createPhaseModel(robot_model, foot_holds[0, :], foot_placements[0, :], foot_orientations_rot_matrix[0, :, :])]
    cop = np.zeros(3)

    for i in range(len_steps):
        if phase[i] == 0:
            if i == 0:
                for j in range(num_nodes_double_support):
                    tmp_cop = foot_holds[0, :] + (foot_holds[1, :] - foot_holds[0, :]) * (j +
                                                                                          1) / num_nodes_double_support
                    cop = np.vstack((cop, tmp_cop))
                    tmp_model = createPhaseModel(robot_model,
                                                 tmp_cop,
                                                 foot_placements[i, :],
                                                 foot_orientations_rot_matrix[i, :, :],
                                                 support=phase[i],
                                                 xref=np.array([0.0, 0.0, com_height + tmp_cop[2], 0.1, 0.0, 0.0]))
                    locoModel += [tmp_model]
            elif i == len(phase) - 1:
                for j in range(num_nodes_double_support):
                    tmp_cop = foot_holds[i - 1, :] + (foot_holds[i, :] -
                                                      foot_holds[i - 1, :]) * (j + 1) / num_nodes_double_support
                    cop = np.vstack((cop, tmp_cop))
                    tmp_model = createPhaseModel(robot_model,
                                                 tmp_cop,
                                                 foot_placements[i, :],
                                                 foot_orientations_rot_matrix[i, :, :],
                                                 support=phase[i],
                                                 xref=np.array([0.0, 0.0, com_height + tmp_cop[2], 0.1, 0.0, 0.0]))
                    locoModel += [tmp_model]
            else:
                for j in range(num_nodes_double_support):
                    tmp_cop = foot_holds[i - 1, :] + (foot_holds[i + 1, :] -
                                                      foot_holds[i - 1, :]) * (j + 1) / num_nodes_double_support
                    cop = np.vstack((cop, tmp_cop))
                    tmp_model = createPhaseModel(robot_model,
                                                 tmp_cop,
                                                 foot_placements[i, :],
                                                 foot_orientations_rot_matrix[i, :, :],
                                                 support=phase[i],
                                                 xref=np.array([0.0, 0.0, com_height + tmp_cop[2], 0.1, 0.0, 0.0]))
                    locoModel += [tmp_model]

        if phase[i] == 1 or phase[i] == -1:
            locoModel += [
                createPhaseModel(robot_model,
                                 foot_holds[i, :],
                                 foot_placements[i, :],
                                 foot_orientations_rot_matrix[i, :, :],
                                 support=phase[i],
                                 xref=np.array([0.0, 0.0, com_height + tmp_cop[2], 0.1, 0.0, 0.0]))
            ] * num_nodes_single_support
            for j in range(num_nodes_single_support):
                cop = np.vstack((cop, foot_holds[i, :]))
    x_ref_final = np.array([(foot_placements[-1, 0] + foot_placements[-1, 3]) / 2,
                            (foot_placements[-1, 1] + foot_placements[-1, 4]) / 2,
                            (foot_placements[-1, 2] + foot_placements[-1, 5]) / 2 + com_height, 0., 0., 0.])
    mT = createTerminalModel(robot_model,
                             np.array([0.1, 0.0, 0.00]),
                             foot_placements[-1, :],
                             foot_orientations_rot_matrix[-1, :, :],
                             xref=x_ref_final)

    ##############################  Plot&Interpolate the data  #################################################
    # import matplotlib.pyplot as plt
    # plt.plot(cop[:,1])
    # plt.show()

    u_init = np.array([931.95, 0.125, 0.125, 0.125, 0.125, 0.125, 0.125, 0.125])
    x_init = np.array([0.0, 0.0, com_height, 0.0, 0.0, 0.0])
    problem = crocoddyl.ShootingProblem(x_init, locoModel, mT)
    problem.nthreads = 1
    solver = crocoddyl.SolverBoxFDDP(problem)
    log = crocoddyl.CallbackLogger()
    solver.setCallbacks([log, crocoddyl.CallbackVerbose()])
    t0 = time.time()
    solver.solve([x_init] * (problem.T + 1), [u_init] * problem.T, 5)  # x init, u init, max iteration
    print('Time of iteration consumed', time.time() - t0)
    vhip.plotComMotion(solver.xs, solver.us)

    ########### Interpolates the trajectory with given frequency #######################################
    # swing_height = 0.10
    # swing_height_offset = 0.01
    # com_gain = [30.0, 7.5, 1.0]
    #
    # com_pos = np.array([x[:3] for x in solver.xs])
    # com_vel = np.array([x[3:] for x in solver.xs])
    # com_acc = np.vstack((np.diff(com_vel, axis=0) / dt, np.zeros(3)))
    # total_time = np.cumsum(support_durations)[-1]
    # time = np.linspace(0, total_time, com_pos.shape[0])
    # # sampling based on the frequency
    # f_pos_x = interp1d(time,
    #                    com_pos[:, 0],
    #                    fill_value=(com_pos[0, 0], com_pos[-1, 0]),
    #                    bounds_error=False)
    # f_pos_y = interp1d(time,
    #                    com_pos[:, 1],
    #                    fill_value=(com_pos[0, 1], com_pos[-1, 1]),
    #                    bounds_error=False)
    # f_pos_z = interp1d(time,
    #                    com_pos[:, 2],
    #                    fill_value=(com_pos[0, 2], com_pos[-1, 2]),
    #                    bounds_error=False)
    # f_vel_x = interp1d(time,
    #                    com_vel[:, 0],
    #                    fill_value=(com_vel[0, 0], com_vel[-1, 0]),
    #                    bounds_error=False)
    # f_vel_y = interp1d(time,
    #                    com_vel[:, 1],
    #                    fill_value=(com_vel[0, 1], com_vel[-1, 1]),
    #                    bounds_error=False)
    # f_vel_z = interp1d(time,
    #                    com_vel[:, 2],
    #                    fill_value=(com_vel[0, 2], com_vel[-1, 2]),
    #                    bounds_error=False)
    # f_acc_x = interp1d(time,
    #                    com_acc[:, 0],
    #                    fill_value=(com_acc[0, 0], com_acc[-1, 0]),
    #                    bounds_error=False)
    # f_acc_y = interp1d(time,
    #                    com_acc[:, 1],
    #                    fill_value=(com_acc[0, 1], com_acc[-1, 1]),
    #                    bounds_error=False)
    # f_acc_z = interp1d(time,
    #                    com_acc[:, 2],
    #                    fill_value=(com_acc[0, 2], com_acc[-1, 2]),
    #                    bounds_error=False)
    # freq = 500
    # sample_num = int(freq * total_time)
    # time_sample = np.linspace(0, total_time, sample_num)
    #
    # com_traj_sample = np.vstack(
    #     (time_sample, f_pos_x(time_sample), f_pos_y(time_sample),
    #      f_pos_z(time_sample), f_vel_x(time_sample), f_vel_y(time_sample),
    #      f_vel_z(time_sample), f_acc_x(time_sample), f_acc_y(time_sample),
    #      f_acc_z(time_sample)))
    # # plt.plot(com_traj_sample[7, :])
    # # plt.show()
    #
    # import trajectory_publisher
    #
    # rospy.init_node('listener', anonymous=True)
    #
    # trajectory_publisher.publish_all(com_traj_sample.T, support_durations,
    #                                  support_indexes, foot_placements,
    #                                  foot_orientations, swing_height,
    #                                  swing_height_offset, com_gain)

    # np.savez('crocoddyl_data.npz', com_traj=com_traj_sample, support_durations=support_durations, support_indexes=support_indexes,
    #         foot_placements=foot_placements, foot_orientations=foot_orientations)
    # crocoddyl.plotOCSolution(log.xs[:], log.us)
    # crocoddyl.plotConvergence(log.costs, log.u_regs, log.x_regs, log.grads, log.stops, log.steps)

    # np.save('../foot_holds.npy', np.concatenate((foot_holds, np.array([phase]).T), axis=1))
    # np.save('../com_traj.npy', solver.xs)
    # np.save('../control_traj.npy', solver.us)

    # solver.
