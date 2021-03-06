#from qt import *

from HardwareRepository import HardwareRepository
from HardwareRepository.BaseHardwareObjects import Device

from HardwareRepository.Command.Tango import DeviceProxy

import logging
import os
import time

class PX1Energy(Device) :
    
    energy_state = {'ALARM': 'error',
                    'FAULT': 'error',
                    'RUNNING': 'moving',
                    'MOVING' : 'moving',
                    'STANDBY' : 'ready',
                    'DISABLE' : 'error',
                    'UNKNOWN': 'unknown',
                    'EXTRACT': 'outlimits'}

    def init(self):
        
        self.moving = False
        
        self.doBacklashCompensation = False

        self.current_energy = None
        self.current_state = None
        
        try :    
            self.monodevice = DeviceProxy(self.getProperty("mono_device"))
        except :    
            self.errorDeviceInstance(self.getProperty("mono_device"))

        # Nom du device bivu (Energy to gap) : necessaire pour amelioration du positionnement de l'onduleur (Backlash)
        self.und_device = DeviceProxy(self.getProperty("undulator_device"))
        self.doBacklashCompensation = self.getProperty("backlash")
            
        # parameters for polling     
        self.isConnected()

        self.energy_chan = self.getChannelObject("energy") 
        self.energy_chan.connectSignal("update", self.energyChanged)

        self.stop_cmd = self.getCommandObject("stop")

        self.state_chan = self.getChannelObject("state") 
        self.state_chan.connectSignal("update", self.stateChanged)

    def connectNotify(self, signal):
        if signal == 'energyChanged':
            logging.getLogger("HWR").debug("PX1Energy. connectNotify. sending energy value %s" % self.get_energy())
            self.energyChanged( self.get_energy() )

        if signal == 'stateChanged' :    
            logging.getLogger("HWR").debug("PX1Energy. connectNotify. sending state value %s" % self.get_state())
            self.stateChanged( self.get_state() )

        self.setIsReady(True)
         
    def stateChanged(self,value):
        str_state = str(value)
        if str_state == 'MOVING':
            self.moveEnergyCmdStarted()

        if self.current_state == 'MOVING' or self.moving == True:
            if str_state != 'MOVING' :
                self.moveEnergyCmdFinished() 
                     
        self.current_state = str_state
        self.emit('stateChanged', self.energy_state[str_state])
        
    # function called during polling
    def energyChanged(self,value):

        if self.current_energy is not None and abs(self.current_energy - value) < 0.0001:
            return

        self.current_energy = value     

        wav = self.getCurrentWavelength()
        if wav is not None:
            self.emit('energyChanged', (value,wav))
            
    def isSpecConnected(self):
        return True
    
    def isConnected(self):
        return True

    def sConnected(self):
        self.emit('connected', ())
      
    def sDisconnected(self):
        self.emit('disconnected', ())
    
    def isDisconnected(self):
        return True
        
    # Definit si la beamline est a energie fixe ou variable      
    def can_move_energy(self):
        return  True
        
    def getPosition(self):
        return self.getCurrentEnergy()

    def getCurrentEnergy(self):
        return self.get_energy()
    
    def get_energy(self):
        return self.energy_chan.getValue()

    def getState(self):
        return self.get_state()

    def get_state(self):
        return str(self.state_chan.getValue())

    def getEnergyComputedFromCurrentGap(self):
        return self.und_device.energy
    
    def getCurrentUndulatorGap(self):
        return self.und_device.gap
            
    def get_wavelength(self):
        return self.monodevice.read_attribute("lambda").value

    def getCurrentWavelength(self):
        return self.get_wavelength()
        
    def getLimits(self):
        return self.getEnergyLimits()

    def get_energy_limits(self):
        chan_info = self.energy_chan.getInfo()
        return (float(chan_info.min_value), float(chan_info.max_value))
    
    def get_wavelength_limits(self):
        energy_min, energy_max = self.getEnergyLimits()
       
        # max is min and min is max
        max_lambda = self.energy_to_lambda(energy_min)
        min_lambda = self.energy_to_lambda(energy_max)

        return (min_lambda, max_lambda)
            
    def energy_to_lambda(self, value):
        # conversion is done by mono device
        self.monodevice.simEnergy = value
        return self.monodevice.simLambda

    def lambda_to_energy(self, value):
        # conversion is done by mono device
        self.monodevice.simLambda = value
        return self.monodevice.simEnergy

    def move_energy(self, value, wait=False):
        value = float(value)
    
        backlash = 0.1 # en mm
        gaplimite = 5.5  # en mm
        
        if self.get_state() != "MOVING":
            if self.doBacklashCompensation:
                try :
                    # Recuperation de la valeur de gap correspondant a l'energie souhaitee
                    #self.und_device.autoApplyComputedParameters = False
                    self.und_device.energy = value
                    newgap = self.und_device.computedGap
                    actualgap = self.und_device.gap
                    
#                    self.und_device.autoApplyComputedParameters = True
                
                    while str(self.und_device.State()) == 'MOVING':
                        time.sleep(0.2)

                    # On applique le backlash que si on doit descendre en gap	    
                    if newgap < actualgap + backlash:
                        # Envoi a un gap juste en dessous (backlash)    
                        if newgap-backlash > gaplimite :
                            self.und_device.gap = newgap - backlash
                            while str(self.und_device.State()) == 'MOVING':
                                time.sleep(0.2)

                            self.energy_chan.setValue(value)
                        else :
                            self.und_device.gap = gaplimite
                            self.und_device.gap = newgap + backlash
                        time.sleep(1)
                except : 
                    logging.getLogger("HWR").error("%s: Cannot move undulator U20 : State device = %s", self.name(), str(self.und_device.State()))
                
            try :
                self.energy_chan.setValue(value)
                return value
            except :           
                logging.getLogger("HWR").error("%s: Cannot move Energy : State device = %s", self.name(), self.get_state())
            
        else : 
            logging.getLogger("HWR").error("%s: Cannot move Energy : State device = %s", self.name(), self.get_state())
            
    def move_wavelength(self, value, wait=False):
        egy_value = self.lambda_to_energy( float(value) )
        logging.getLogger("HWR").debug("%s: Moving wavelength to : %s (egy to %s" % (self.name(), value, egy_value))
        self.move_energy(egy_value)
	return value
    
    def cancelMoveEnergy(self):
        self.stop_cmd()
        self.moving = False
            
    def energyLimitsChanged(self,limits):
        egy_min, egy_max = limits

        lambda_min = self.energy_to_lambda(egy_min)
        lambda_max = self.energy_to_lambda(egy_max)

        wav_limits=(lambda_min, lambda_max)

        self.emit('energyLimitsChanged', (limits,))

        if None not in wav_limits:
            self.emit('wavelengthLimitsChanged', (wav_limits,))
        else:
            self.emit('wavelengthLimitsChanged', (None,))
            
    def moveEnergyCmdReady(self):
        if not self.moving :
            self.emit('moveEnergyReady', (True,))
            
    def moveEnergyCmdNotReady(self):
        if not self.moving :
            self.emit('moveEnergyReady', (False,))
            
    def moveEnergyCmdStarted(self):
        self.moving = True
        self.emit('moveEnergyStarted', ())
        
    def moveEnergyCmdFailed(self):
        self.moving = False
        self.emit('moveEnergyFailed', ())
        
    def moveEnergyCmdAborted(self):
        self.moving = False
    
    def moveEnergyCmdFinished(self):
        self.moving = False
        self.emit('moveEnergyFinished',())
        
    def getPreviousResolution(self):
        return (None, None)
        
    def restoreResolution(self):
        return (False,"Resolution motor not defined")

    getEnergyLimits = get_energy_limits
    getWavelengthLimits = get_wavelength_limits
    canMoveEnergy = can_move_energy
    startMoveEnergy = move_energy
    startMoveWavelength = move_wavelength
    

def test_hwo(hwo):
    print hwo.getPosition()
    print hwo.getCurrentWavelength()
    print hwo.get_energy_limits()
    print hwo.getCurrentUndulatorGap()
