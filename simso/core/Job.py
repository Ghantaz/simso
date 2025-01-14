# coding=utf-8

from SimPy.Simulation import Process, hold, passivate
from simso.core.JobEvent import JobEvent
from math import ceil


class Job(Process):
    """The Job class simulate the behavior of a real Job. This *should* only be
    instantiated by a Task."""

    def __init__(self, task, name, pred, monitor, etm, sim):
        """
        Args:
            - `task`: The parent :class:`task <simso.core.Task.Task>`.
            - `name`: The name for this job.
            - `pred`: If the task is not periodic, pred is the job that \
            released this one.
            - `monitor`: A monitor is an object that log in time.
            - `etm`: The execution time model.
            - `sim`: :class:`Model <simso.core.Model>` instance.

        :type task: GenericTask
        :type name: str
        :type pred: bool
        :type monitor: Monitor
        :type etm: AbstractExecutionTimeModel
        :type sim: Model
        """
        Process.__init__(self, name=name, sim=sim)
        self._task = task
        self._pred = pred
        self.instr_count = 0  # Updated by the cache model.
        self._computation_time = 0
        self._last_exec = None
        self._n_instr = task.n_instr
        self._start_date = None
        self._end_date = None
        self._is_preempted = False
        self._activation_date = self.sim.now_ms()
        self._absolute_deadline = self.sim.now_ms() + task.deadline
        self._aborted = False
        self._sim = sim
        self._monitor = monitor
        self._etm = etm
        self._was_running_on = task.cpu

        # Support for mixed criticality execution time
        self.current_wcet = None
        self.current_level_wcet = None

        # Allow applying attack. Set to True on preempt. 
        # Made False the first time an attack takes place
        self.apply_attack = False

        # Allow attack when first arrives. Only allowed once
        # Set to True to apply attack when job released and not yet executed
        self.apply_first_attack=True

        # Scheduler has updated the wcet and has determined 
        # criticality change will take place due to this job
        self.impending_up_level = False

        self._on_activate()

        self.context_ok = True  # The context is ready to be loaded.

    def is_active(self):
        """
        Return True if the job is still active.
        """
        return self._end_date is None

    def _on_activate(self):
        self._monitor.observe(JobEvent(self, JobEvent.ACTIVATE))
        self._sim.logger.log(self.name + " Activated.", kernel=True)
        self._etm.on_activate(self)

    def _on_execute(self):
        self._last_exec = self.sim.now()
        
        # Ensure that the first attack is no more possible
        # Since the job is beginning to execute
        self.apply_first_attack = False

        self._etm.on_execute(self)
        if self._is_preempted:
            self._is_preempted = False
        if self.apply_attack:
            self.apply_attack = False

        self.cpu.was_running = self

        self._monitor.observe(JobEvent(self, JobEvent.EXECUTE, self.cpu))
        self._sim.logger.log("{} Executing on {}".format(
            self.name, self._task.cpu.name), kernel=True)

    def _on_stop_exec(self):
        if self._last_exec is not None:
            self._computation_time += self.sim.now() - self._last_exec
        self._last_exec = None

    def _on_preempted(self):
        self._on_stop_exec()
        self._etm.on_preempted(self)
        self._is_preempted = True
        self.apply_attack = True
        self._was_running_on = self.cpu

        self._monitor.observe(JobEvent(self, JobEvent.PREEMPTED))
        self._sim.logger.log(self.name + " Preempted! ret: " +
                             str(self.interruptLeft), kernel=True)


    def _on_self_preempted(self):
        self._on_stop_exec()
        self._etm.on_preempted(self)
        self._is_preempted = True
        self.apply_attack = True
        self._was_running_on = self.cpu

        self._monitor.observe(JobEvent(self, JobEvent.PREEMPTED))
        self._sim.logger.log(self.name + " Self Preempted! ret: " +
                             str(self.interruptLeft), kernel=True)


    def _on_criticality_change(self):
        self._task.cpu.criticality_signal(self)

    def _on_terminated(self):
        self._on_stop_exec()
        self._etm.on_terminated(self)

        self._end_date = self.sim.now()
        self._monitor.observe(JobEvent(self, JobEvent.TERMINATED))
        self._task.end_job(self)
        self._task.cpu.terminate(self)
        self._sim.logger.log(self.name + " Terminated.", kernel=True)

    def _on_abort(self):
        self._on_stop_exec()
        self._etm.on_abort(self)
        self._end_date = self.sim.now()
        self._aborted = True
        self._monitor.observe(JobEvent(self, JobEvent.ABORTED))
        self._task.end_job(self)
        self._task.cpu.terminate(self)
        self._sim.logger.log("Job " + str(self.name) + " aborted! ret:" + str(self.ret))

    def is_running(self):
        """
        Return True if the job is currently running on a processor.
        Equivalent to ``self.cpu.running == self``.

        :rtype: bool
        """
        return self.cpu.running == self

    def abort(self):
        """
        Abort this job. Warning, this is currently only used by the Task when
        the job exceeds its deadline. It has not be tested from outside, such
        as from the scheduler.
        """
        self._on_abort()

    @property
    def aborted(self):
        """
        True if the job has been aborted.

        :rtype: bool
        """
        return self._aborted

    @property
    def exceeded_deadline(self):
        """
        True if the end_date is greater than the deadline or if the job was
        aborted.
        """
        return (self._absolute_deadline * self._sim.cycles_per_ms <
                self._end_date or self._aborted)

    @property
    def start_date(self):
        """
        Date (in ms) when this job started executing
        (different than the activation).
        """
        return self._start_date

    @property
    def end_date(self):
        """
        Date (in ms) when this job finished its execution.
        """
        return self._end_date

    @property
    def response_time(self):
        if self._end_date:
            return (float(self._end_date) / self._sim.cycles_per_ms -
                    self._activation_date)
        else:
            return None

    @property
    def ret(self):
        """
        Remaining execution time in ms.
        """
        return self.wcet - self.actual_computation_time

    @property
    def laxity(self):
        """
        Dynamic laxity of the job in ms.
        """
        return (self.absolute_deadline - self.ret
                ) * self.sim.cycles_per_ms - self.sim.now()

    @property
    def computation_time(self):
        """
        Time spent executing the job in ms.
        """
        return float(self.computation_time_cycles) / self._sim.cycles_per_ms

    @property
    def computation_time_cycles(self):
        """
        Time spent executing the job.
        """
        if self._last_exec is None:
            return int(self._computation_time)
        else:
            return (int(self._computation_time) +
                    self.sim.now() - self._last_exec)

    @property
    def actual_computation_time(self):
        """
        Computation time in ms as if the processor speed was 1.0 during the
        whole execution.
        """
        return float(
            self.actual_computation_time_cycles) / self._sim.cycles_per_ms

    @property
    def actual_computation_time_cycles(self):
        """
        Computation time as if the processor speed was 1.0 during the whole
        execution.
        """
        return self._etm.get_executed(self)

    @property
    def cpu(self):
        """
        The :class:`processor <simso.core.Processor.Processor>` on which the
        job is attached. Equivalent to ``self.task.cpu``.
        """
        return self._task.cpu

    @property
    def task(self):
        """The :class:`task <simso.core.Task.Task>` for this job."""
        return self._task

    @property
    def data(self):
        """
        The extra data specified for the task. Equivalent to
        ``self.task.data``.
        """
        return self._task.data

    @property
    def wcet(self):
        """
        Worst-Case Execution Time in milliseconds.
        Equivalent to ``self.task.wcet``.
        """
        if(self.current_wcet == None):    # Fresh job
            return self._task.wcet
        else:
            return self.current_wcet

    # Add support for modifying execution time by the scheduler
    # This is for supporting mixed criticality
    @wcet.setter
    def wcet(self, new_wcet):
        self.current_wcet = new_wcet

    @property
    def activation_date(self):
        """
        Activation date in milliseconds for this job.
        """
        return self._activation_date

    # Current criticality level execution time
    # Needed to detect overrun
    
    @property
    def this_level_wcet(self):
        '''
        Current level execution time. Saved for posterity
        '''
        if(self.current_level_wcet ==  None): # Fresh Job
            print("ERR! This should never have been called before being set")
            exit(-1)
        return self.current_level_wcet

    @this_level_wcet.setter
    def this_level_wcet(self, new_level_wcet):
        self.current_level_wcet = new_level_wcet

    @property
    def absolute_deadline(self):
        """
        Absolute deadline in milliseconds for this job. This is the activation
        date + the relative deadline.
        """
        return self._absolute_deadline

    @property
    def absolute_deadline_cycles(self):
        return self._absolute_deadline * self._sim.cycles_per_ms

    @property
    def period(self):
        """Period in milliseconds. Equivalent to ``self.task.period``."""
        return self._task.period

    @property
    def deadline(self):
        """
        Relative deadline in milliseconds.
        Equivalent to ``self.task.deadline``.
        """
        return self._task.deadline

    @property
    def pred(self):
        return self._pred

    def activate_job(self):
        self._start_date = self.sim.now()
        # Notify the OS.
        self._task.cpu.activate(self)

        # While the job's execution is not finished.
        while self._end_date is None:
            # Wait an execute order.
            yield passivate, self

            # Execute the job.
            if not self.interrupted():
                self._on_execute()
                # ret is a duration lower than the remaining execution time.
                ret = self._etm.get_ret(self)
                current_level_ret = self._etm.get_current_level_ret(self)

                if(self.wcet > self.current_level_wcet):
                    #print("Job" + self.name + " Up level")
                    self.impending_up_level = True

                while ret > 0:
                    if(self.impending_up_level):
                        yield hold, self, int(ceil(current_level_ret))
                    else:
                        yield hold, self, int(ceil(ret))
                    if not self.interrupted():
                        # Yield op completed. Check which yield was used
                        # If impending one, then let the scheduler know 
                        # That criticality change must occur
                        # Fixed for multiple victims. Criticality level change is 
                        # Instantaneous now
                        if(self.impending_up_level):
                            #print("Job "+self.name + " now informing scheduler of criticality change")
                            self.impending_up_level = False
                            #self.sim.scheduler.up_level = True
                            self._on_criticality_change()
                            #print("Job "+self.name + " Crit change waiting for go ahead")
                            self._on_self_preempted()
                            self.interruptReset()
                            break
                        
                        # If executed without interruption for ret cycles.
                        ret = self._etm.get_ret(self)
                    else:
                        self._on_preempted()
                        self.interruptReset()
                        break

                if ret <= 0:
                    # End of job.
                    self._on_terminated()

            else:
                self.interruptReset()