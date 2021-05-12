import crocoddyl
import numpy as np
import time
import math
import utils

class DifferentialActionModelCentroidal(crocoddyl.DifferentialActionModelAbstract):
    def __init__(self, costs, m):
        # x: pos, orientation, lin_vel, ang_vel
        crocoddyl.DifferentialActionModelAbstract.__init__(self, crocoddyl.StateVector(12), 6) # nu = 6 = 3*2 feet
        self.uNone = np.zeros(self.nu)

        self.m = m
        self.Ig = np.diag([0.00578574, 0.01938108, 0.02476124]) # can be get from URDF

        self.g = np.zeros(6)
        self.g[2] = -9.81
        self.costs = costs
        # by row: xyz; by column: contact point
        self.footholds = np.array(
            [[0.0, 0.0],
             [0.085, -0.085],
             [0.0, 0.0]])
        #Normal vector for friction cone
        self.nsurf = np.array([0., 0., 1.]).T # flat ground
        self.S = np.ones(2)# I want to use bipedal robot at first

        self.u_lb = np.array([-2.0*self.m*self.g, -2.0*self.m*self.g, 0.0, -2.0*self.m*self.g, -2.0*self.m*self.g, 0.0])
        self.u_ub = np.array([2.0*self.m*self.g, 2.0*self.m*self.g, 2.0*self.m*self.g, 2.0*self.m*self.g, 2.0*self.m*self.g, 2.0*self.m*self.g])

    def calc(self, data, x, u):
        # Levers Arms used in B
        data.Level_arm = self.footholds - np.array(x[:3]).transpose() # broadcast x
        H = utils.euler_matrix(x[3],x[4],x[5]) # rotation matrix
        R = H[:3,:3]
        data.I_inv = np.linalg.inv(np.dot(R, self.gI))
        for i in range(2):
            # if feet in touch with ground
            if self.S[i] != 0:
                data.B[:3, (i*3):((i+1)*3)] = np.identity(3)/self.m #
                data.B[-3:, (i*3):((i+1)*3)] = np.dot(data.I_inv, utils.getSkew(data.Level_arm[:, i])) # another term needs added, for now it is OK
        # Compute friction cone
        # self.costFriction(u)
        data.xout = np.dot(data.B,u) + self.g

        # compute the cost residual
        self.costs.calc(data.costs, x, u)
        data.cost = data.costs.cost

    def calcDiff(self, data, x, u):
        for i in range(2):
            if self.S[i] != 0:
                data.derivative_B[-3:, 0] = - np.dot(data.I_inv, np.cross([1, 0, 0], [u[3 * i], u[3 * i + 1],
                                                                                                u[3 * i + 2]]))  # \x
                data.derivative_B[-3:, 1] = - np.dot(data.I_inv, np.cross([0, 1, 0], [u[3 * i], u[3 * i + 1],
                                                                                                u[3 * i + 2]]))  # \y
                data.derivative_B[-3:, 2] = - np.dot(data.I_inv, np.cross([0, 0, 1], [u[3 * i], u[3 * i + 1],
                                                                                                u[3 * i + 2]]))  # \z
        data.Fx[:,:] = data.derivative_B[:,:]
        data.Fu[:,:] = data.B[:,:]
        self.costs.calcDiff(data.costs, x, u)

    def createData(self):
        data = DifferentialActionDataCentroidal(self)
        data.B = np.zeros((12, 6))
        data.Level_arm = np.zeros((3, 2)) # 2 contact points
        data.derivative_B = np.zeros((12, 6))
        data.I_inv = np.identity(3)
        return data

    def updateModel(self, foothold, nsurf, contact_selection, Ig=np.diag([0.00578574, 0.01938108, 0.02476124])):
        self.footholds = foothold
        self.nsurf = nsurf
        self.S = contact_selection
        self.Ig = Ig

class DifferentialActionDataCentroidal(crocoddyl.DifferentialActionDataAbstract):
    def __init__(self, model):
        crocoddyl.DifferentialActionDataAbstract.__init__(self, model)
        shared_data = crocoddyl.DataCollectorAbstract()
        self.costs = model.costs.createData(shared_data)
        self.costs.shareMemory(self)

def createPhaseModel(foothold, contact, xref=np.array([0.0, 0.0, 0.86, 0.0, 0.0, 0.0]), nsurf=np.array([0.,0.,1.]).T, mu=0.7, Wx=np.array([0., 0., 10., 10., 10., 10.]), Wu=np.array([0., 50., 50., 1.]),
                     wxreg=1, wureg=5, wutrack=50, wxbox=1, dt=2e-2):
    state = crocoddyl.StateVector(12)
    runningCosts = crocoddyl.CostModelSum(state, 6)
    uRef = np.hstack([np.zeros(1), foothold])# ???
    xRef = xref
    nSurf = nsurf
    Mu = mu
    # cone = crocoddyl.FrictionCone(nSurf, Mu, 1, False)
    ub = np.hstack([foothold, np.zeros(3)]) + np.array([0.3, 0.055, 0.95, 7., 7., 3])
    lb = np.hstack([foothold, np.zeros(3)]) + np.array([-0.3, -0.055, 0.75, -7., -7., -3])
    runningCosts.addCost("comBox", crocoddyl.CostModelResidual(state, crocoddyl.ActivationModelQuadraticBarrier(crocoddyl.ActivationBounds(lb, ub)), crocoddyl.ResidualModelState(state, xRef, 6)), wxbox)
    runningCosts.addCost("comReg", crocoddyl.CostModelResidual(state, crocoddyl.ActivationModelWeightedQuad(Wx), crocoddyl.ResidualModelState(state, xRef, 6)), wxreg)
    runningCosts.addCost("uTrack", crocoddyl.CostModelControl(state, crocoddyl.ActivationModelWeightedQuad(Wu), uRef), wureg) ## ||u||^2
    runningCosts.addCost("uReg", crocoddyl.CostModelResidual(state, crocoddyl.ResidualModelControl(state,6)), wutrack) ## ||u||^2
    model = DifferentialActionModelCentroidal(runningCosts)
    model.updateModel(foothold=foothold,contact_selection=contact)
    return crocoddyl.IntegratedActionModelEuler(model, dt)

def createTerminalModel(foothold):
    return createPhaseModel(foothold, xref=np.array([0.0, 0.0, 0.86, 0.0, 0.0, 0.0]), Wx=np.array([0., 0., 100., 30., 30., 150.]), wxreg=1e6, dt=0.)

m1 = createPhaseModel(np.array([0.0, 0.0, 0.00]))
m2 = createPhaseModel(np.array([0.0, -0.08, 0.00]))
m3 = createPhaseModel(np.array([0.1, 0.0, 0.00]))
m4 = createPhaseModel(np.array([0.2, 0.08, 0.00]))
m5 = createPhaseModel(np.array([0.2, 0.0, 0.00]))
mT = createTerminalModel(np.array([0.2, 0.0, 0.00]))

foot_holds = np.array([[0.0, 0.0, 0.0],[0.0, -0.08, 0.05],[0.1, 0.0, 0.1],[0.2, 0.08, 0.15],[0.2, 0.0, 0.2]])
phase = np.array([0, 1, 0, -1, 0]) # 0: double, 1: left, -1: right

num_nodes_single_support = 50
num_nodes_double_support = 25

locoModel = [m1]*num_nodes_double_support
locoModel += [m2]*num_nodes_single_support
locoModel += [m3]*num_nodes_double_support
locoModel += [m4]*num_nodes_single_support
locoModel += [m5]*num_nodes_double_support

x_init = np.array([0.0, 0.0, 0.86, 0.0, 0.0, 0.0])
# x_init = np.zeros(6)
problem = crocoddyl.ShootingProblem(x_init, locoModel, mT)
solver = crocoddyl.SolverBoxFDDP(problem)
# solver = crocoddyl.SolverFDDP(problem)
log = crocoddyl.CallbackLogger()
solver.setCallbacks([log, crocoddyl.CallbackVerbose()])
u_init = np.array([931.95, 0.0, 0.0, 0.001])
t0 = time.time()
# u_init = [m.quasiStatic(d, x_init) for m,d in zip(problem.runningModels, problem.runningDatas)]
solver.solve([x_init]*(problem.T + 1), [u_init]*problem.T, 100) # x init, u init, max iteration
# solver.solve()  # x init, u init, max iteration

print('Time of iteration consumed', time.time()-t0)

crocoddyl.plotOCSolution(log.xs[:], log.us)
# crocoddyl.plotConvergence(log.costs, log.u_regs, log.x_regs, log.grads, log.stops, log.steps)


def plotComMotion(xs, us):
    import matplotlib.pyplot as plt
    plt.rcParams["pdf.fonttype"] = 42
    plt.rcParams["ps.fonttype"] = 42
    cx = [x[0] for x in xs]
    cy = [x[1] for x in xs]
    cz = [x[2] for x in xs]
    cxdot = [x[3] for x in xs]
    cydot = [x[4] for x in xs]
    czdot = [x[5] for x in xs]
    f_z = [u[0] for u in us]
    u_x = [u[1] for u in us]
    u_y = [u[2] for u in us]
    u_z = [u[3] for u in us]

    plt.plot(cx)
    plt.show()
    plt.plot(cy)
    plt.show()
    plt.plot(cz)
    plt.show()
    plt.plot(u_x)
    plt.show()
    plt.plot(u_y)
    plt.show()


plotComMotion(solver.xs, solver.us)

# solver.
import trajectory_publisher

