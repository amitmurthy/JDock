from datetime import datetime, timedelta

import isodate
from cloud.aws import CloudHost

from jbox_util import unquote
from handlers.handler_base import JBoxHandler
from jbox_container import JBoxContainer
from handlers.auth import AuthHandler
from db.user_v2 import JBoxUserV2
from db.invites import JBoxInvite
from db.accounting_v2 import JBoxAccountingV2


class AdminHandler(JBoxHandler):
    def get(self):
        sessname = unquote(self.get_cookie("sessname"))
        jbox_cookie = AuthHandler.get_session_cookie(self)

        if (None == sessname) or (len(sessname) == 0) or (None == jbox_cookie):
            self.send_error()
            return

        user_id = jbox_cookie['u']
        cont = JBoxContainer.get_by_name(sessname)

        if cont is None:
            self.send_error()
            return

        if self.do_logout(cont):
            response = {'code': 0, 'data': ''}
            self.write(response)
            return

        juliaboxver, _upgrade_available = self.get_upgrade_available(cont)

        user = JBoxUserV2(user_id)

        is_admin = sessname in self.config("admin_sessnames", [])
        manage_containers = is_admin or user.has_role(JBoxUserV2.ROLE_MANAGE_CONTAINERS)
        show_report = is_admin or user.has_role(JBoxUserV2.ROLE_ACCESS_STATS)
        invites_perm = is_admin or user.has_role(JBoxUserV2.ROLE_MANAGE_INVITES)

        sections = []
        loads = []
        report = {}
        report_span = 'day'

        action = self.get_argument("action", None)
        #invite_code = self.request.get("invite_code", None)
        if action == "invites_report" and invites_perm:
            self.write(dict(
                code=0,
                data=[obj for obj in JBoxInvite.table().scan()]))
            return

        if manage_containers:
            sections, loads = self.do_containers()

        if show_report:
            today = datetime.now()
            if self.get_argument('range', 'day') == 'week':
                dates = [today - timedelta(days=i) for i in range(6, -1, -1)]
                report_span = 'week'
            else:
                dates = [today]
            report = JBoxAccountingV2.get_stats(dates)

        d = dict(
            manage_containers=manage_containers,
            show_report=show_report,
            invites_perm=invites_perm,
            report_span=report_span,
            sessname=sessname,
            user_id=user_id,
            created=isodate.datetime_isoformat(cont.time_created()),
            started=isodate.datetime_isoformat(cont.time_started()),
            allowed_till=isodate.datetime_isoformat((cont.time_started() + timedelta(seconds=self.config('expire')))),
            mem=cont.get_memory_allocated(),
            cpu=cont.get_cpu_allocated(),
            disk=cont.get_disk_allocated(),
            expire=self.config('expire'),
            sections=sections,
            loads=loads,
            report=report,
            juliaboxver=juliaboxver
        )

        self.rendertpl("ipnbadmin.tpl", d=d, cfg=self.config())

    def do_logout(self, cont):
        logout = self.get_argument('logout', False)
        if logout == 'me':
            cont.async_backup_and_cleanup()
            return True
        return False

    @staticmethod
    def get_upgrade_available(cont):
        cont_images = cont.get_image_names()
        juliaboxver = cont_images[0]
        if (JBoxContainer.DCKR_IMAGE in cont_images) or ((JBoxContainer.DCKR_IMAGE + ':latest') in cont_images):
            upgrade_available = None
        else:
            upgrade_available = JBoxContainer.DCKR_IMAGE
            if ':' not in upgrade_available:
                upgrade_available += ':latest'
        return juliaboxver, upgrade_available

    def do_containers(self):
        sections = []
        loads = []

        iac = []
        ac = []
        sections.append(["Active", ac])
        sections.append(["Inactive", iac])

        delete_id = self.get_argument("delete_id", '')
        stop_id = self.get_argument("stop_id", '')
        stop_all = (self.get_argument('stop_all', None) is not None)

        if stop_all:
            all_containers = JBoxContainer.DCKR.containers(all=False)
            for c in all_containers:
                cont = JBoxContainer(c['Id'])
                cname = cont.get_name()

                if None == cname:
                    self.log_info("Admin: Not stopping unknown " + cont.debug_str())
                elif cname not in self.config("protected_docknames"):
                    cont.stop()

        elif not (stop_id == ''):
            cont = JBoxContainer(stop_id)
            cont.stop()
        elif not (delete_id == ''):
            cont = JBoxContainer(delete_id)
            cont.delete()

        # get them all again (in case we deleted some)
        jsonobj = JBoxContainer.DCKR.containers(all=all)
        for c in jsonobj:
            o = dict()
            o["Id"] = c["Id"][0:12]
            o["Status"] = c["Status"]
            if ("Names" in c) and (c["Names"] is not None):
                o["Name"] = c["Names"][0]
            else:
                o["Name"] = "/None"

            if (c["Ports"] is None) or (c["Ports"] == []):
                iac.append(o)
            else:
                ac.append(o)

        # get cluster loads
        average_load = CloudHost.get_cluster_average_stats('Load')
        if None != average_load:
            loads.append({'instance': 'Average', 'load': average_load})

        machine_loads = CloudHost.get_cluster_stats('Load')
        if None != machine_loads:
            for n, v in machine_loads.iteritems():
                loads.append({'instance': n, 'load': v})

        return sections, loads
