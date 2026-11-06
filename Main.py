API_VERSION = 'API_v1.0'  # This is Mandatory!
MOD_NAME = 'ArmorInfo'  # Your Mod Name
from DataEnum import EnumX, EnumIX, EnumVIII, EnumEX, EnumVII, EnumVI, EnumV, EnumIV, EnumIII

try:
    import battle, events, ui, callbacks, utils
except:
    pass

POLL = 0.1
KEYS = ('citadel', 'bow_st', 'cas', 'cas_deck', 'outer', 'dd_cas', 'cas_t', 'bow_st_s')
NO_VALUE = ('N/A', 'null', '', None, '0', 0)
BLANK = {'shipIDS': 'null', 'shipType': '',
         'citadel': 'N/A', 'bow_st': 'N/A', 'cas': 'N/A', 'cas_deck': 'N/A',
         'outer': 'N/A', 'dd_cas': 'N/A', 'cas_t': 'N/A', 'bow_st_s': 'N/A'}

try:
    _STR = basestring
except NameError:
    _STR = str


def log(m):
    try:
        utils.logInfo('[ArmorInfo] ' + str(m))
    except:
        pass


def g(o, *names):
    # 属性 / 下标两种写法都试，返回第一个非空值
    if o is None:
        return None
    for n in names:
        v = None
        try:
            v = o[n]
        except:
            v = None
        if v is None or v == '':
            try:
                v = getattr(o, n)
            except:
                v = None
        if v is not None and v != '':
            return v
    return None


def looksLikeShipID(sid):
    # PASB210 / JSB046 这类 shortName 才认，避免把玩家名、占位名当船 ID
    if not isinstance(sid, _STR) or len(sid) < 4 or len(sid) > 24:
        return False
    if ':' in sid or '.' in sid or ' ' in sid:
        return False
    digits = 0
    for ch in sid:
        if ch.isdigit():
            digits += 1
    return digits >= 2


class ArmorInfo(object):
    def __init__(self):
        self._enums = {11: EnumEX, 10: EnumX, 9: EnumIX, 8: EnumVIII, 7: EnumVII,
                       6: EnumVI, 5: EnumV, 4: EnumIV, 3: EnumIII}
        self._defSet = {'citadel': 'N/A', 'bow_st': 'N/A', 'cas': 'N/A', 'cas_deck': 'N/A',
                        'outer': 'N/A', 'dd_cas': 'N/A', 'cas_t': 'N/A', 'bow_st_s': 'N/A'}
        # 短名 -> 该船所在的分级表；等级/舰种拿不到时（PvE 的 AI 档案常常没有）用它 O(1) 定位
        self._index = {}
        for lv in sorted(self._enums.keys()):
            cls = self._enums[lv]
            try:
                names = dir(cls)
            except:
                continue
            for k in names:
                if k.startswith('_') or k in self._index:
                    continue
                if isinstance(getattr(cls, k, None), dict):
                    self._index[k] = cls
        self._cacheIDS = None
        self._cacheSet = None
        self._vary = None
        self._uiId = None
        self._shownIDS = None
        self._logBudget = 0
        self._probed = {}
        self._probeBudget = 0
        events.onBattleShown(self.onBattleStart)
        events.onBattleQuit(self.onQuit)

    # ------------------------------------------------ 战斗生命周期
    def onBattleStart(self, *args):
        self._stopPoll()
        self._resetState()
        self._logBudget = 6
        self._probeBudget = 3
        try:
            self._uiId = uiId = ui.createUiElement()
            ui.addDataComponentWithId(uiId, 'ArmorInfoModEntityKey', {})
        except:
            self._uiId = None
        self._vary = callbacks.callback(POLL, self.tick)

    def onQuit(self, *args):
        self._stopPoll()
        self._showBlank('battle quit')
        self._resetState()
        if self._uiId is not None:
            try:
                ui.deleteUiElement(self._uiId)
            except:
                pass
            self._uiId = None

    def _stopPoll(self):
        if self._vary is not None:
            try:
                callbacks.cancel(self._vary)
            except:
                pass
            self._vary = None

    def _resetState(self):
        self._cacheIDS = None
        self._cacheSet = None
        self._shownIDS = None
        self._probed = {}

    # ------------------------------------------------ 轮询
    def tick(self, *args):
        # 任何异常都不能冒出去：冒出去整条回调链就断了，面板会永久停在旧数据
        try:
            self._update()
        except:
            pass

    def _update(self):
        if self._uiId is None:
            return
        target = self._targetShip()
        if target is None:
            self._showBlank('no target')
            return
        shipID, shipType, shipLv, src = self._identify(target)
        if not shipID:
            self._showBlank('unresolved %s' % self._nameOf(target))
            return
        if shipID == self._shownIDS:
            return
        self._push(shipID, shipType, shipLv, src)

    def _targetShip(self):
        try:
            target = battle.getObserverShip()
        except:
            target = None
        if target is not None:
            return target
        try:
            return battle.getSelfPlayerShip()
        except:
            return None

    def _nameOf(self, target):
        try:
            return str(g(target, 'playerName', 'player_name', 'name'))
        except:
            return '?'

    # ------------------------------------------------ 认船
    def _identify(self, target):
        # 返回 (shipID, shipType, shipLv, 数据来源)
        pName = g(target, 'playerName', 'player_name')
        name = pName if pName is not None else g(target, 'name')

        # 1) 玩家档案：真人目标和旧版行为一致；bot 可能只在 name 上挂船名
        info = self._playerInfoByName(pName)
        if info is None and name is not None and name != pName:
            info = self._playerInfoByName(name)
        ship = self._shipFromInfo(info, name)
        if ship:
            return ship[0], ship[1], ship[2], 'playerInfo'

        # 2) 全名册：PvE 的 bot 在这里，但 PlayerInfo.shipInfo 常为 None
        entry = self._playerInfoFromRoster(name, target)
        ship = self._shipFromInfo(entry, name)
        if ship:
            return ship[0], ship[1], ship[2], 'roster'

        # 3) getPlayerShipInfo(playerID)：档案里 shipInfo 为空时的官方取法
        ship = self._shipFromPlayerShipInfo(target, entry)
        if ship:
            return ship[0], ship[1], ship[2], 'playerShipInfo'

        # 4) 实体自身：舰船标识挂在 vehicle 上
        ship = self._shipFromEntity(target, name)
        if ship:
            return ship[0], ship[1], ship[2], 'entity'

        self._probe(target, info, entry)
        return None, None, None, ''

    def _playerInfoByName(self, name):
        if name is None:
            return None
        try:
            return battle.getPlayerInfoByName(name)
        except:
            return None

    def _iterRoster(self):
        # getPlayersInfo() 的 .keys() 在部分版本不可用，按 items / keys / values 依次退让
        try:
            roster = battle.getPlayersInfo()
        except:
            return None, []
        if roster is None:
            return None, []
        pairs = []
        try:
            for k, v in roster.items():
                pairs.append((k, v))
            return roster, pairs
        except:
            pairs = []
        try:
            for k in list(roster.keys()):
                try:
                    pairs.append((k, roster[k]))
                except:
                    pass
            if pairs:
                return roster, pairs
        except:
            pairs = []
        try:
            for v in list(roster.values()):
                pairs.append((None, v))
            if pairs:
                return roster, pairs
        except:
            pairs = []
        try:
            for k in roster:
                try:
                    pairs.append((k, roster[k]))
                except:
                    pass
        except:
            pass
        return roster, pairs

    def _playerInfoFromRoster(self, name, target):
        roster, pairs = self._iterRoster()
        if not pairs:
            return None
        pid = g(target, 'dbId', 'db_id', 'playerId', 'player_id', 'id')
        for k, entry in pairs:
            if entry is None:
                continue
            if name is not None and (k == name or g(entry, 'playerName', 'player_name', 'name') == name):
                return entry
            if pid is not None and (k == pid or g(entry, 'dbId', 'db_id', 'playerId', 'player_id', 'id') == pid):
                return entry
        return None

    def _shipFromPlayerShipInfo(self, target, entry):
        cands = []
        for src in (target, entry):
            if src is None:
                continue
            for attr in ('dbId', 'db_id', 'playerId', 'player_id', 'id'):
                v = g(src, attr)
                if v is not None:
                    cands.append(v)
        tried = []
        for pid in cands:
            if pid in tried:
                continue
            tried.append(pid)
            try:
                info = battle.getPlayerShipInfo(pid)
            except:
                info = None
            ship = self._shipFromInfo(info, None)
            if ship:
                return ship
            if len(tried) >= 4:
                break
        return None

    def _shipFromInfo(self, info, name):
        if info is None:
            return None
        sources = (g(info, 'shipInfo', 'ship_info'), g(info, 'vehicleType', 'vehicle_type'), info)
        for src in sources:
            ship = self._shipFromFields(src, name, False)
            if ship:
                return ship
        for src in sources:
            ship = self._shipFromFields(src, name, True)
            if ship:
                return ship
        return None

    def _shipFromFields(self, src, name, allowName):
        if src is None:
            return None
        if allowName:
            attrs = ('vehicleTypeName', 'vehicle_type_name', 'name')
        else:
            attrs = ('shortName', 'short_name', 'typeName', 'type_name',
                     'shipConfigName', 'ship_config_name', 'shipName', 'ship_name')
        sid = g(src, *attrs)
        if self._accept(sid, name):
            return (sid,
                    g(src, 'subtype', 'type', 'shipType', 'ship_type'),
                    g(src, 'level', 'tier', 'levelId', 'level_id'))
        return None

    def _shipFromEntity(self, target, name):
        sources = (g(target, 'publicInfo', 'vehiclePublicInfo', 'public_info'),
                   g(target, 'vehicleType', 'vehicle_type'),
                   g(target, 'typeInfo', 'type_info'),
                   g(target, 'shipConfig', 'ship_config'),
                   g(target, 'typeDescriptor', 'type_descriptor'),
                   g(target, 'shipInfo', 'ship_info'),
                   target)
        for src in sources:
            ship = self._shipFromFields(src, name, False)
            if ship:
                return ship
        for src in sources:
            ship = self._shipFromFields(src, name, True)
            if ship:
                return ship
        return None

    def _probe(self, target, info, entry):
        # 认不出时打一行诊断：把候选字段原样记下来，方便照着日志补字段名
        key = self._nameOf(target)
        if key in self._probed or self._probeBudget <= 0:
            return
        self._probed[key] = 1
        self._probeBudget -= 1
        roster, pairs = self._iterRoster()
        ids = []
        for src in (target, entry):
            if src is None:
                continue
            for attr in ('dbId', 'db_id', 'playerId', 'player_id', 'id'):
                v = g(src, attr)
                if v is not None:
                    ids.append('%s=%r' % (attr, v))
        line = 'probe target=%r ids=[%s] info=%s entry=%s roster=%d' % (
            key, ', '.join(ids),
            self._brief(info), self._brief(entry), len(pairs) if pairs else 0)
        log(line[:600])

    def _brief(self, o):
        if o is None:
            return 'None'
        parts = []
        for attr in ('name', 'playerName', 'dbId', 'playerId', 'teamId', 'isBot',
                     'shortName', 'typeName', 'subtype', 'level', 'shipInfo'):
            v = g(o, attr)
            if v is not None:
                parts.append('%s=%r' % (attr, v))
        if not parts:
            return '<no fields>'
        return '{' + ', '.join(parts) + '}'

    def _accept(self, sid, name):
        # 不比对玩家名：PvE 里 PlayerInfo.name 本身就是船短名，
        # 靠 looksLikeShipID 的格式判断即可（纯玩家名没有两位数字，会被挡掉）
        if not isinstance(sid, _STR) or sid == '':
            return False
        return looksLikeShipID(sid)

    # ------------------------------------------------ 出数据
    def _push(self, shipID, shipType, shipLv, src):
        try:
            shipType = str(shipType) if shipType else ''
        except:
            shipType = ''
        if not shipType:
            shipType = self._inferShipType(shipID, shipLv)
        armor = self._armorFor(shipID, shipType, shipLv)
        self._shownIDS = shipID
        payload = {'shipIDS': 'IDS_' + shipID, 'shipType': shipType}
        for k in KEYS:
            payload[k] = armor.get(k, 'N/A')
        try:
            ui.updateUiElementData(self._uiId, payload)
        except:
            pass
        if self._logBudget > 0:
            self._logBudget -= 1
            log('show %s type=%s lv=%s via=%s' % (shipID, shipType, shipLv, src))

    def _showBlank(self, reason):
        if self._shownIDS == 'null' or self._uiId is None:
            return
        self._shownIDS = 'null'
        try:
            ui.updateUiElementData(self._uiId, dict(BLANK))
        except:
            pass
        if self._logBudget > 0:
            self._logBudget -= 1
            log('hide (%s)' % reason)

    def _armorFor(self, shipID, shipType, shipLv):
        if shipID == self._cacheIDS and self._cacheSet is not None:
            return self._cacheSet
        cls = self._enumFor(shipLv, shipType)
        entry = self._entry(cls, shipID)
        if entry is None and cls is None:
            # 等级/舰种压根没给（PvE AI 档案常见）：用初始化时建好的索引定位
            # 给了但不匹配时不用索引 —— 那是数据不一致，宁可不显示
            entry = self._entry(self._index.get(shipID), shipID)
        if entry is None:
            entry = self._defSet
        out = {}
        for k in KEYS:
            v = entry.get(k, 'N/A')
            out[k] = 'N/A' if v in NO_VALUE else str(v)
        self._cacheIDS = shipID
        self._cacheSet = out
        return out

    def _inferShipType(self, shipID, shipLv):
        # EnumEX 里同时收 ★ 和航母；等级不是 11 却在这张表里 -> 航母
        # （ub 靠 shipType 决定显示"机库"还是"甲板"，实体兜底时拿不到 subtype，只能这样推）
        if self._entry(self._enums.get(11), shipID) is None:
            return ''
        try:
            if int(shipLv) == 11:
                return ''
        except:
            pass
        return 'AirCarrier'

    def _enumFor(self, shipLv, shipType):
        try:
            st = str(shipType) if shipType else ''
        except:
            st = ''
        if 'AirCarrier' in st or 'aircarrier' in st:
            return self._enums.get(11)
        try:
            lv = int(shipLv)
        except:
            lv = None
        return self._enums.get(lv)

    def _entry(self, cls, shipID):
        if cls is None:
            return None
        try:
            entry = getattr(cls, shipID, None)
        except:
            return None
        return entry if isinstance(entry, dict) else None


ArmorInfoMod = ArmorInfo()
