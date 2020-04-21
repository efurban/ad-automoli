"""AutoMoLi.
   Automatic Motion Lights

  @benleb / https://github.com/benleb/ad-automoli
  @https://github.com/efurban/ad-automoli
"""

__version__ = "0.6.1"

from datetime import time
from typing import Any, Dict, List, Optional, Set, Union

import hassapi as hass
import math
import time as t

APP_NAME = "AutoMoLi"
APP_ICON = "💡"
APP_REQUIREMENTS = {"adutils~=0.4.10"}

ON_ICON = APP_ICON
OFF_ICON = "🌑"
DAYTIME_SWITCH_ICON = "⏰"

# default values
DEFAULT_NAME = "daytime"
DEFAULT_LIGHT_SETTING = 100
DEFAULT_DELAY = 150
DEFAULT_DAYTIMES = [
    dict(starttime="05:30", name="morning", light=25),
    dict(starttime="07:30", name="day", light=100),
    dict(starttime="20:30", name="evening", light=90),
    dict(starttime="22:30", name="night", light=0),
]
DEFAULT_FADE_DURATION = 3

EVENT_MOTION_XIAOMI = "xiaomi_aqara.motion"

KEYWORD_LIGHTS = "light."
KEYWORD_MOTION = "binary_sensor.motion_sensor_"
KEYWORD_HUMIDITY = "sensor.humidity_"
KEYWORD_ILLUMINANCE = "sensor.illumination_"


# install requirements
def _install_packages(required: Set[str]) -> bool:
    """Install packages from PyPi."""
    from subprocess import run
    from sys import executable
    flags = ["--quiet", "--disable-pip-version-check", "--no-cache-dir", "--upgrade"]
    return run([executable, "-m", "pip", "install", *flags, *required]).returncode == 0


_install_packages(APP_REQUIREMENTS)

from adutils import ADutils, hl, py37_or_higher  # noqa # isort:skip


class AutoMoLi(hass.Hass):  # type: ignore
    """Automatic Motion Lights."""

    def initialize(self) -> None:
        """Initialize a room with AutoMoLi."""
        self.adu = ADutils(APP_NAME, config={}, icon=APP_ICON, ad=self)

        # python version check
        if not py37_or_higher:
            raise ValueError

        # set room
        self.room = str(self.args.get("room"))

        # sensor entities
        self.sensors = {
            "motion": self.args.pop("motion", set()),
            "humidity": self.args.get("humidity", set()),
            "illuminance": self.args.get("illuminance", set()),
        }

        # state values
        self.states = {
            "motion_on": self.args.get("motion_state_on", None),
            "motion_off": self.args.get("motion_state_off", None),
        }

        # threshold values
        self.thresholds = {
            "humidity": self.args.get("humidity_threshold"),
            "illuminance": self.args.get("illuminance_threshold"),
        }

        # on/off switch via input.boolean
        self.disable_switch_entity = self.args.get("disable_switch_entity", [])
        # print (*self.disable_switch_entity)

        # fade on/off delay 
        self.fadeSetting = {
            "on": self.args.get("fade_on", DEFAULT_FADE_DURATION),
            "off": self.args.get("fade_off", DEFAULT_FADE_DURATION)
        }

        # currently active daytime settings
        self.active: Dict[str, Union[int, str]] = {}

        # lights_off callback handle
        self._handle = None

        # define light entities switched by automoli
        self.lights: Set[str] = self.args.get("lights", set())
        if not self.lights:
            room_light_group = f"light.{self.room}"
            if self.entity_exists(room_light_group):
                self.lights.add(room_light_group)
            else:
                self.lights.update(self.find_sensors(KEYWORD_LIGHTS))
            if not self.lights:
                raise ValueError(f"No lights available, sorry! ('{KEYWORD_LIGHTS}')")

        # define sensor entities monitored by automoli
        if not self.sensors["motion"]:
            self.sensors["motion"].update(self.find_sensors(KEYWORD_MOTION))
            if not self.sensors["motion"]:
                raise ValueError(f"No sensors given/found, sorry! ('{KEYWORD_MOTION}')")

        # enumerate humidity sensors if threshold given
        if self.thresholds["humidity"] and not self.sensors["humidity"]:
            self.sensors["humidity"].update(self.find_sensors(KEYWORD_HUMIDITY))
            if not self.sensors["humidity"]:
                self.log(f"No humidity sensors available → disabling blocker.")
                self.thresholds["humidity"] = None

        # enumerate illuminance sensors if threshold given
        if self.thresholds["illuminance"] and not self.sensors["illuminance"]:
            self.sensors["illuminance"].update(self.find_sensors(KEYWORD_ILLUMINANCE))
            if not self.sensors["illuminance"]:
                self.log(f"No illuminance sensors available → disabling blocker.")
                self.thresholds["illuminance"] = None

        # use user-defined daytimes if available
        daytimes = self.build_daytimes(self.args.get("daytimes", DEFAULT_DAYTIMES))

        # set up event listener for each sensor
        for sensor in self.sensors["motion"]:

            # listen to xiaomi sensors by default
            if not any([self.states["motion_on"], self.states["motion_off"]]):
                self.listen_event(
                    self.motion_event, event=EVENT_MOTION_XIAOMI, entity_id=sensor
                )
                self.refresh_timer()

                # do not use listen event and listen state below together
                continue

            # on/off-only sensors without events on every motion
            if all([self.states["motion_on"], self.states["motion_off"]]):
                self.listen_state(
                    self.motion_detected, entity=sensor, new=self.states["motion_on"]
                )
                self.listen_state(
                    self.motion_cleared, entity=sensor, new=self.states["motion_off"]
                )

        # display settings
        self.args.setdefault("listeners", self.sensors["motion"])
        self.args.setdefault(
            "sensors_illuminance", list(self.sensors["illuminance"])
        ) if self.sensors["illuminance"] else None
        self.args.setdefault(
            "sensors_humidity", list(self.sensors["humidity"])
        ) if self.sensors["humidity"] else None
        self.args["daytimes"] = daytimes

        # init adutils
        # self.adu = ADutils(
        #     APP_NAME, self.args, icon=APP_ICON, ad=self, show_config=True
        # )
        self.adu.show_info(self.args)

    def switch_daytime(self, kwargs: Dict[str, Any]) -> None:
        """Set new light settings according to daytime."""
        daytime = kwargs.get("daytime")

        if daytime is not None:
            self.active = daytime
            if not kwargs.get("initial"):

                delay = daytime["delay"]
                light_setting = daytime["light_setting"]
                if isinstance(light_setting, str):
                    is_scene = True
                    # if its a ha scene, remove the "scene." part
                    if "." in light_setting:
                        light_setting = (light_setting.split("."))[1]
                else:
                    is_scene = False

                self.adu.log(
                    f"set {hl(self.room.capitalize())} to {hl(daytime['daytime'])} → "
                    f"{'scene' if is_scene else 'brightness'}: {hl(light_setting)}"
                    f"{'' if is_scene else '%'}, delay: {hl(delay)}sec",
                    icon=DAYTIME_SWITCH_ICON,
                )

    ########################## twu: additional features #############################################
    def eval_disable_switch_conf(self, confLine):
        # map(str.strip, my_list)
        conf = confLine.split(';')  # split each conf elements 
        entity, disableStat = map(str.strip, conf[0].split(',')) # get entity and disableState
        
        rtn = False # True: to disable

        # 1. check the status
        # if status is not true: no need to check att 
        # print ('State to satisfy: {} => {}, curr: {}'.format(entity, disableStat, self.get_state(entity)))
        # for attConf in conf[1:]:
        #     att, attStat = map(str.strip, attConf.split(','))
        #     # debug message 
        #     print ('Attribute to disable auto: {}: {} => {}, curr: {}'.format(entity, att, attStat, self.get_state(entity, att)))

        # print (' ==> {} att len = {}'.format(self.get_state(entity), len(conf[1:])) ) 
        if self.get_state(entity) == disableStat:
            # else go through the attributes if any 
            statCond = True
            attCond = False 
            if (len(conf[1:]) == 0):
                attCond = True
            
            for attConf in conf[1:]:
                att, attStat = map(str.strip, attConf.split(','))
                currAtt = self.get_state(entity, att)
                if (currAtt == attStat or (currAtt is None and "None" == attStat)):
                    print('attributes satisfied to disable automation')
                    attCond = True
                    break
            if (attCond and statCond):
                rtn = True
        else:
            rtn = False
        # print ('return val of eval_disable_switch_conf {} => {} '.format(confLine, rtn))
        return rtn        

    async def fade(self, entity, direction, targetBrightnessPct, duration):
        # bound = self.active["light_setting"] if direction == "up" else 0  #target brightness in setting 
        targetBrightness = targetBrightnessPct * 255 / 100
        adjFrequency = 0.2
        adjPoints = duration / adjFrequency
        initBrightness = await self.get_state(entity, "brightness")
        initBrightness = initBrightness if initBrightness is not None else 0
        step = int(math.ceil ((float(targetBrightness) - float(initBrightness) if direction == "up" else -1.0 * float(initBrightness)) / float(adjPoints) ) ) # using int division
        
        # print (initBrightness, targetBrightness, step, direction, duration)
        # print (type(initBrightness), type(targetBrightness), type(step))
        
        if (step != 0):
            for b in range(int(initBrightness), int(targetBrightness), int(step)):
                await self.call_service(
                    "homeassistant/turn_on",
                    entity_id = entity,
                    brightness = b,
                )
                t.sleep(adjFrequency)
        await self.call_service(
                "homeassistant/turn_on",
                entity_id = entity,
                brightness = int(targetBrightness),
            )
        # self.adu.log(
        #     f"{hl(self.room.capitalize())} turned {hl(f'on')} → "
        #     f"brightness: {hl(self.active['light_setting'])}%",
        #     icon=ON_ICON,
        #) 
    ########################## twu: end of additional  #############################################

    def motion_cleared(
        self, entity: str, attribute: str, old: str, new: str, kwargs: Dict[str, Any]
    ) -> None:
        # starte the timer if motion is cleared
        if all(
            [
                self.get_state(sensor) == self.states["motion_off"]
                for sensor in self.sensors["motion"]
            ]
        ):
            # all motion sensors off, starting timer
            self.refresh_timer()
        else:
            if self._handle:
                # cancelling active timer
                self.cancel_timer(self._handle)

    def motion_detected(
        self, entity: str, attribute: str, old: str, new: str, kwargs: Dict[str, Any]
    ) -> None:
        # wrapper function

        if self._handle:
            # cancelling active timer
            self.cancel_timer(self._handle)

        # calling motion event handler
        data: Dict[str, Any] = {"entity_id": entity, "new": new, "old": old}
        self.motion_event("state_changed_detection", data, kwargs)

    def motion_event(
        self, event: str, data: Dict[str, str], kwargs: Dict[str, Any]
    ) -> None:
        """Handle motion events."""
        self.adu.log(
            f"received '{event}' event from "
            f"'{data['entity_id'].replace(KEYWORD_MOTION, '')}'",
            level="DEBUG",
        )

        # check if automoli is disabled via home assistant entity
        for lineStr in self.disable_switch_entity:
            if self.eval_disable_switch_conf(lineStr):
                self.adu.log(f"AutoMoLi disabled via {lineStr}",)
                return

        # if self.get_state(self.disable_switch_entity) == "off":
        #     self.adu.log(f"AutoMoLi disabled via {self.disable_switch_entity}",)
        #     return

        # turn on the lights if not already
        if not any([self.get_state(light) == "on" for light in self.lights]):
            self.lights_on()
        else:
            self.adu.log(
                f"light in {self.room.capitalize()} already on → refreshing the timer",
                level="DEBUG",
            )

        if event != "state_changed_detection":
            self.refresh_timer()

    def refresh_timer(self) -> None:
        """Refresh delay timer."""
        self.cancel_timer(self._handle)
        if self.active["delay"] != 0:
            self._handle = self.run_in(self.lights_off, self.active["delay"])

    def lights_on(self) -> None:
        """Turn on the lights."""
        if self.thresholds["illuminance"]:
            blocker = []
            for sensor in self.sensors["illuminance"]:
                try:
                    if float(self.get_state(sensor)) >= self.thresholds["illuminance"]:
                        blocker.append(sensor)
                except ValueError as error:
                    self.adu.log(
                        f"could not parse illuminance '{self.get_state(sensor)}' from "
                        f"'{sensor}': {error}"
                    )
                    return

            if blocker:
                self.adu.log(
                    f"According to {hl(' '.join(blocker))} its already bright enough"
                )
                return

        if isinstance(self.active["light_setting"], str):

            for entity in self.lights:

                if self.active["is_hue_group"] and self.get_state(
                    entity_id=entity, attribute="is_hue_group"
                ):
                    self.call_service(
                        "hue/hue_activate_scene",
                        group_name=self.friendly_name(entity),
                        scene_name=self.active["light_setting"],
                    )
                    continue

                item = entity

                if self.active["light_setting"].startswith("scene."):
                    item = self.active["light_setting"]

                # self.turn_on(item)
                self.call_service("homeassistant/turn_on", entity_id=item)

            self.adu.log(
                f"{hl(self.room.capitalize())} turned {hl(f'on')} → "
                f"{'hue' if self.active['is_hue_group'] else 'ha'} scene: "
                f"{hl(self.active['light_setting'].replace('scene.', ''))}",
                icon=ON_ICON,
            )

        elif isinstance(self.active["light_setting"], int):

            if self.active["light_setting"] == 0:
                self.lights_off(dict())

            else:
                for entity in self.lights:
                    if entity.startswith("switch."):
                        self.call_service("homeassistant/turn_on", entity_id=entity)
                    else:
                        ####################################### call fade on
                        # self.call_service(
                        #     "homeassistant/turn_on",
                        #     entity_id=entity,
                        #     brightness_pct=self.active["light_setting"],
                        # )
                        # entity, direction, targetBrightnessPct, duration
                        targetBrightnessPct = self.active['light_setting']
                        fadeDuration = self.fadeSetting["on"]
                        
                        self.create_task(self.fade(entity, "up", targetBrightnessPct, fadeDuration))

                        self.adu.log(
                            f"{hl(self.room.capitalize())} turned {hl(f'on')} → "
                            f"brightness: {hl(self.active['light_setting'])}%",
                            icon=ON_ICON,
                        )

        else:
            raise ValueError(
                f"invalid brightness/scene: {self.active['light_setting']!s} "
                f"in {self.room}"
            )

    def lights_off(self, kwargs: Dict[str, Any]) -> None:
        """Turn off the lights."""

        # check if automoli is disabled via home assistant entity
        for lineStr in self.disable_switch_entity:
            if self.eval_disable_switch_conf(lineStr):
                self.adu.log(f"AutoMoLi disabled via {lineStr}",)
                return

        # if self.get_state(self.disable_switch_entity) == "off":
        #     self.adu.log(f"AutoMoLi disabled via {self.disable_switch_entity}",)
        #     return

        blocker: Any = None

        if self.thresholds["humidity"]:
            blocker = [
                sensor
                for sensor in self.sensors["humidity"]
                if float(self.get_state(sensor)) >= self.thresholds["humidity"]
            ]
            blocker = blocker.pop() if blocker else None

        # turn off if not blocked
        if blocker:
            self.refresh_timer()
            self.adu.log(
                f"🛁 no motion in {hl(self.room.capitalize())} since "
                f"{hl(self.active['delay'])}s → "
                f"but {hl(float(self.get_state(blocker)))}%RH > "
                f"{self.thresholds['humidity']}%RH"
            )
        else:
            self.cancel_timer(self._handle)
            if any([(self.get_state(entity)) == "on" for entity in self.lights]):
                for entity in self.lights:
                    # self.turn_off(entity)
                    ################## call fade 
                    self.create_task(self.fade(entity, "down", 0, self.fadeSetting["off"]))
                self.adu.log(
                    f"no motion in {hl(self.room.capitalize())} since "
                    f"{hl(self.active['delay'])}s → turned {hl(f'off')}",
                    icon=OFF_ICON,
                )

    def find_sensors(self, keyword: str) -> List[str]:
        """Find sensors by looking for a keyword in the friendly_name."""
        return [
            sensor
            for sensor in self.get_state()
            if keyword in sensor
            and self.room in (self.friendly_name(sensor)).lower().replace("ü", "u")
        ]

    def build_daytimes(
        self, daytimes: List[Any]
    ) -> Optional[List[Dict[str, Union[int, str]]]]:
        starttimes: Set[time] = set()
        delay = int(self.args.get("delay", DEFAULT_DELAY))

        for idx, daytime in enumerate(daytimes):
            dt_name = daytime.get("name", f"{DEFAULT_NAME}_{idx}")
            dt_delay = daytime.get("delay", delay)
            dt_light_setting = daytime.get("light", DEFAULT_LIGHT_SETTING)
            dt_is_hue_group = (
                isinstance(dt_light_setting, str)
                and not dt_light_setting.startswith("scene.")
                and any(
                    [
                        self.get_state(entity_id=entity, attribute="is_hue_group")
                        for entity in self.lights
                    ]
                )
            )

            dt_start: time
            try:
                # dt_start = time.fromisoformat(str(daytime.get("starttime")))
                dt_start = self.parse_time(daytime.get("starttime"), aware=True)
                print (f'{daytime.get("starttime")} => start time = {dt_start}')
            except ValueError as error:
                raise ValueError(f"missing start time in daytime '{dt_name}': {error}")

            # configuration for this daytime
            daytime = dict(
                daytime=dt_name,
                delay=dt_delay,
                starttime=dt_start.isoformat(),  # datetime is not serializable
                light_setting=dt_light_setting,
                is_hue_group=dt_is_hue_group,
            )

            # info about next daytime
            # next_dt_start = time.fromisoformat(
            #     str(daytimes[(idx + 1) % len(daytimes)].get("starttime"))
            # )
            next_dt_start = daytimes[(idx + 1) % len(daytimes)].get("starttime")
            # collect all start times for sanity check
            if dt_start in starttimes:
                raise ValueError(
                    f"Start times of all daytimes have to be unique! "
                    f"Duplicate found: {dt_start}",
                )

            starttimes.add(dt_start)

            # check if this daytime should ne active now
            if self.now_is_between(str(dt_start), str(next_dt_start)):
                self.switch_daytime(dict(daytime=daytime, initial=True))
                self.args["active_daytime"] = daytime.get("daytime")

            # schedule callbacks for daytime switching
            self.run_daily(
                self.switch_daytime, dt_start, random_start=-10, **dict(daytime=daytime)
            )

        return daytimes
