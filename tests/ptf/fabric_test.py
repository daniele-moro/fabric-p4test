# Copyright 2013-present Barefoot Networks, Inc.
# Copyright 2018-present Open Networking Foundation
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#
import struct

from p4.v1 import p4runtime_pb2
from ptf import testutils as testutils
from scapy.layers.inet import IP
from scapy.layers.l2 import Ether, Dot1Q

from base_test import P4RuntimeTest, stringify, mac_to_binary, ipv4_to_binary

DEFAULT_PRIORITY = 10

FORWARDING_TYPE_BRIDGING = 0
FORWARDING_TYPE_UNICAST_IPV4 = 2

DEFAULT_MPLS_TTL = 64
MIN_PKT_LEN = 80

UDP_GTP_PORT = 2152

ETH_TYPE_ARP = 0x0806
ETH_TYPE_IPV4 = 0x0800

MAC_MASK = ":".join(["ff"] * 6)
SWITCH_MAC = "00:00:00:00:aa:01"
HOST1_MAC = "00:00:00:00:00:01"
HOST2_MAC = "00:00:00:00:00:02"
HOST3_MAC = "00:00:00:00:00:03"
HOST1_IPV4 = "10.0.1.1"
HOST2_IPV4 = "10.0.2.1"
HOST3_IPV4 = "10.0.3.1"
HOST4_IPV4 = "10.0.4.1"
S1U_ENB_IPV4 = "119.0.0.10"
S1U_SGW_IPV4 = "140.0.0.2"
UE_IPV4 = "16.255.255.252"

VLAN_ID_1 = 100
VLAN_ID_2 = 200


def make_gtp(msg_len, teid, flags=0x30, msg_type=0xff):
    """Convenience function since GTP header has no scapy support"""
    return struct.pack(">BBHL", flags, msg_type, msg_len, teid)


def pkt_mac_swap(pkt):
    orig_dst = pkt[Ether].dst
    pkt[Ether].dst = pkt[Ether].src
    pkt[Ether].src = orig_dst
    return pkt


def pkt_route(pkt, mac_dst):
    pkt[Ether].src = pkt[Ether].dst
    pkt[Ether].dst = mac_dst
    return pkt


def pkt_add_vlan(pkt, vlan_vid=10, vlan_pcp=0, dl_vlan_cfi=0):
    return Ether(src=pkt[Ether].src, dst=pkt[Ether].dst) / \
           Dot1Q(prio=vlan_pcp, id=dl_vlan_cfi, vlan=vlan_vid) / \
           pkt[Ether].payload


def pkt_decrement_ttl(pkt):
    if IP in pkt:
        pkt[IP].ttl -= 1
    return pkt


class FabricTest(P4RuntimeTest):
    def setUp(self):
        super(FabricTest, self).setUp()
        self.port1 = self.swports(1)
        self.port2 = self.swports(2)
        self.port3 = self.swports(3)

    def setup_int(self):
        self.send_request_add_entry_to_action(
            "int_egress.int_prep", None, "int_egress.int_transit",
            [("switch_id", stringify(1, 4))])

        req = p4runtime_pb2.WriteRequest()
        for i in xrange(16):
            base = "int_set_header_0003_i"
            mf = self.Exact("hdr.int_header.instruction_mask_0003",
                            stringify(i, 1))
            action = "int_metadata_insert." + base + str(i)
            self.push_update_add_entry_to_action(
                req,
                "int_metadata_insert.int_inst_0003", [mf],
                action, [])
        self.write_request(req)

        req = p4runtime_pb2.WriteRequest()
        for i in xrange(16):
            base = "int_set_header_0407_i"
            mf = self.Exact("hdr.int_header.instruction_mask_0407",
                            stringify(i, 1))
            action = "int_metadata_insert." + base + str(i)
            self.push_update_add_entry_to_action(
                req,
                "int_metadata_insert.int_inst_0407", [mf],
                action, [])
        self.write_request(req)

    def setup_port(self, port_id, vlan_id, tagged=False):
        if tagged:
            self.set_ingress_port_vlan(ingress_port=port_id, vlan_id=vlan_id,
                                       vlan_valid=tagged, new_vlan_id=vlan_id)
        else:
            self.set_ingress_port_vlan(ingress_port=port_id, vlan_id=0,
                                       vlan_valid=False, new_vlan_id=vlan_id)
            self.set_egress_vlan_pop(egress_port=port_id, vlan_id=vlan_id)

    def set_ingress_port_vlan(self, ingress_port, vlan_valid=False,
                              vlan_id=0,
                              new_vlan_id=0):
        ingress_port_ = stringify(ingress_port, 2)
        vlan_valid_ = '\x01' if vlan_valid else '\x00'
        vlan_id_ = stringify(vlan_id, 2)
        vlan_id_mask_ = stringify(4095 if vlan_valid else 0, 2)
        new_vlan_id_ = stringify(new_vlan_id, 2)
        action_name = "set_vlan" if vlan_valid else "push_internal_vlan"
        self.send_request_add_entry_to_action(
            "filtering.ingress_port_vlan",
            [self.Exact("standard_metadata.ingress_port", ingress_port_),
             self.Exact("hdr.vlan_tag.is_valid", vlan_valid_),
             self.Ternary("hdr.vlan_tag.vlan_id", vlan_id_, vlan_id_mask_)],
            "filtering." + action_name, [("new_vlan_id", new_vlan_id_)],
            DEFAULT_PRIORITY)

    def set_egress_vlan_pop(self, egress_port, vlan_id):
        egress_port = stringify(egress_port, 2)
        vlan_id = stringify(vlan_id, 2)
        self.send_request_add_entry_to_action(
            "egress_next.egress_vlan",
            [self.Exact("hdr.vlan_tag.vlan_id", vlan_id),
             self.Exact("standard_metadata.egress_port", egress_port)],
            "egress_next.pop_vlan", [])

    def set_forwarding_type(self, ingress_port, eth_dstAddr, ethertype=0x800,
                            fwd_type=FORWARDING_TYPE_UNICAST_IPV4):
        ingress_port_ = stringify(ingress_port, 2)
        eth_dstAddr_ = mac_to_binary(eth_dstAddr)
        eth_mask_ = mac_to_binary(MAC_MASK)
        ethertype_ = stringify(ethertype, 2)
        fwd_type_ = stringify(fwd_type, 1)
        self.send_request_add_entry_to_action(
            "filtering.fwd_classifier",
            [self.Exact("standard_metadata.ingress_port", ingress_port_),
             self.Ternary("hdr.ethernet.dst_addr", eth_dstAddr_, eth_mask_),
             self.Exact("hdr.vlan_tag.ether_type", ethertype_)],
            "filtering.set_forwarding_type", [("fwd_type", fwd_type_)],
            priority=DEFAULT_PRIORITY)

    def add_bridging_entry(self, vlan_id, eth_dstAddr, eth_dstAddr_mask,
                           next_id):
        vlan_id_ = stringify(vlan_id, 2)
        eth_dstAddr_ = mac_to_binary(eth_dstAddr)
        eth_dstAddr_mask_ = mac_to_binary(eth_dstAddr_mask)
        next_id_ = stringify(next_id, 4)
        self.send_request_add_entry_to_action(
            "forwarding.bridging",
            [self.Exact("hdr.vlan_tag.vlan_id", vlan_id_),
             self.Ternary("hdr.ethernet.dst_addr",
                          eth_dstAddr_, eth_dstAddr_mask_)],
            "forwarding.set_next_id_bridging", [("next_id", next_id_)],
            DEFAULT_PRIORITY)

    def add_forwarding_routing_v4_entry(self, ipv4_dstAddr, ipv4_pLen,
                                        next_id):
        ipv4_dstAddr_ = ipv4_to_binary(ipv4_dstAddr)
        next_id_ = stringify(next_id, 4)
        self.send_request_add_entry_to_action(
            "forwarding.routing_v4",
            [self.Lpm("hdr.ipv4.dst_addr", ipv4_dstAddr_, ipv4_pLen)],
            "forwarding.set_next_id_routing_v4", [("next_id", next_id_)])

    def add_forwarding_acl_cpu_entry(self, eth_type=None, clone=False):
        eth_type_ = stringify(eth_type, 2)
        eth_type_mask = stringify(0xFFFF, 2)
        action_name = "clone_to_cpu" if clone else "punt_to_cpu"
        self.send_request_add_entry_to_action(
            "forwarding.acl",
            [self.Ternary("hdr.vlan_tag.ether_type", eth_type_, eth_type_mask)],
            "forwarding." + action_name, [],
            DEFAULT_PRIORITY)

    def add_next_hop(self, next_id, egress_port):
        next_id_ = stringify(next_id, 4)
        egress_port_ = stringify(egress_port, 2)
        self.send_request_add_entry_to_action(
            "next.simple",
            [self.Exact("fabric_metadata.next_id", next_id_)],
            "next.output_simple", [("port_num", egress_port_)])

    def add_next_multicast(self, next_id, mcast_group_id):
        next_id_ = stringify(next_id, 4)
        mcast_group_id_ = stringify(mcast_group_id, 2)
        self.send_request_add_entry_to_action(
            "next.multicast",
            [self.Exact("fabric_metadata.next_id", next_id_)],
            "next.set_mcast_group", [("gid", mcast_group_id_)])

    def add_next_hop_L3(self, next_id, egress_port, smac, dmac):
        next_id_ = stringify(next_id, 4)
        egress_port_ = stringify(egress_port, 2)
        smac_ = mac_to_binary(smac)
        dmac_ = mac_to_binary(dmac)
        self.send_request_add_entry_to_action(
            "next.simple",
            [self.Exact("fabric_metadata.next_id", next_id_)],
            "next.l3_routing_simple",
            [("port_num", egress_port_), ("smac", smac_), ("dmac", dmac_)])

    def add_vlan_meta(self, next_id, new_vlan_id):
        next_id_ = stringify(next_id, 4)
        vlan_id_ = stringify(new_vlan_id, 2)
        self.send_request_add_entry_to_action(
            "next.vlan_meta",
            [self.Exact("fabric_metadata.next_id", next_id_)],
            "next.set_vlan",
            [("new_vlan_id", vlan_id_)])

    # next_hops is a dictionary mapping mbr_id to (egress_port, smac, dmac)
    # we can break this method into several ones (group creation, etc.) if there
    # is a need when adding new tests in the future
    def add_next_hop_L3_group(self, next_id, grp_id, next_hops=None):
        next_id_ = stringify(next_id, 4)
        if next_hops is not None:
            for mbr_id, params in next_hops.items():
                egress_port, smac, dmac = params
                egress_port_ = stringify(egress_port, 2)
                smac_ = mac_to_binary(smac)
                dmac_ = mac_to_binary(dmac)
                self.send_request_add_member(
                    "next.ecmp_selector", mbr_id, "next.l3_routing_hashed",
                    [("port_num", egress_port_), ("smac", smac_), ("dmac", dmac_)])
        self.send_request_add_group("next.ecmp_selector", grp_id,
                                    grp_size=32, mbr_ids=next_hops.keys())
        self.send_request_add_entry_to_group(
            "next.hashed",
            [self.Exact("fabric_metadata.next_id", next_id_)],
            grp_id)

    def add_next_hop_mpls_v4(self, next_id, egress_port, smac, dmac, label):
        next_id_ = stringify(next_id, 4)
        egress_port_ = stringify(egress_port, 2)
        smac_ = mac_to_binary(smac)
        dmac_ = mac_to_binary(dmac)
        label_ = stringify(label, 3)
        self.send_request_add_entry_to_action(
            "next.simple",
            [self.Exact("fabric_metadata.next_id", next_id_)],
            "next.mpls_routing_v4_simple",
            [("port_num", egress_port_), ("smac", smac_), ("dmac", dmac_),
             ("label", label_)])

    # next_hops is a dictionary mapping mbr_id to (egress_port, smac, dmac,
    # label)
    def add_next_hop_mpls_v4_group(self, next_id, grp_id, next_hops=None):
        next_id_ = stringify(next_id, 4)
        if next_hops is not None:
            for mbr_id, params in next_hops.items():
                egress_port, smac, dmac, label = params
                egress_port_ = stringify(egress_port, 2)
                smac_ = mac_to_binary(smac)
                dmac_ = mac_to_binary(dmac)
                label_ = stringify(label, 3)
                self.send_request_add_member(
                    "next.ecmp_selector", mbr_id, "next.mpls_routing_v4_hashed",
                    [("port_num", egress_port_), ("smac", smac_), ("dmac", dmac_),
                     ("label", label_)])
        self.send_request_add_group("next.ecmp_selector", grp_id,
                                    grp_size=32, mbr_ids=next_hops.keys())
        self.send_request_add_entry_to_group(
            "next.hashed",
            [self.Exact("fabric_metadata.next_id", next_id_)],
            grp_id)

    def add_mcast_group(self, group_id, ports):
        req = p4runtime_pb2.WriteRequest()
        req.device_id = self.device_id
        update = req.updates.add()
        update.type = p4runtime_pb2.Update.INSERT
        pre_entry = update.entity.packet_replication_engine_entry
        mg_entry = pre_entry.multicast_group_entry
        mg_entry.multicast_group_id = group_id
        for port in ports:
            replica = mg_entry.replicas.add()
            replica.egress_port = port
            replica.instance = 0
        return req, self.write_request(req)


class BridgingTest(FabricTest):

    def runBridgingTest(self, tagged1, tagged2, pkt):
        vlan_id = 10
        mac_src = pkt[Ether].src
        mac_dst = pkt[Ether].dst
        self.setup_port(self.port1, vlan_id, tagged1)
        self.setup_port(self.port2, vlan_id, tagged2)
        # miss on filtering.fwd_classifier => bridging
        self.add_bridging_entry(vlan_id, mac_src, MAC_MASK, 10)
        self.add_bridging_entry(vlan_id, mac_dst, MAC_MASK, 20)
        self.add_next_hop(10, self.port1)
        self.add_next_hop(20, self.port2)

        exp_pkt = pkt_decrement_ttl(pkt.copy())
        pkt2 = pkt_mac_swap(pkt.copy())
        exp_pkt2 = pkt_decrement_ttl(pkt2.copy())

        if tagged1:
            pkt = pkt_add_vlan(pkt, vlan_vid=vlan_id)
            exp_pkt2 = pkt_add_vlan(exp_pkt2, vlan_vid=vlan_id)

        if tagged2:
            pkt2 = pkt_add_vlan(pkt2, vlan_vid=vlan_id)
            exp_pkt = pkt_add_vlan(exp_pkt, vlan_vid=vlan_id)

        testutils.send_packet(self, self.port1, str(pkt))
        testutils.send_packet(self, self.port2, str(pkt2))
        testutils.verify_each_packet_on_each_port(
            self, [exp_pkt, exp_pkt2], [self.port2, self.port1])


class ArpBroadcastTest(FabricTest):
    def runArpBroadcastTest(self, tagged_ports, untagged_ports):
        zero_mac_addr = ":".join(["00"] * 6)
        vlan_id = 10
        next_id = vlan_id
        mcast_group_id = vlan_id
        all_ports = tagged_ports + untagged_ports
        arp_pkt = testutils.simple_arp_packet(pktlen=MIN_PKT_LEN - 4)
        # Account for VLAN header size in total pktlen
        vlan_arp_pkt = testutils.simple_arp_packet(vlan_vid=vlan_id, pktlen=MIN_PKT_LEN)
        for port in tagged_ports:
            self.set_ingress_port_vlan(port, True, vlan_id, vlan_id)
        for port in untagged_ports:
            self.set_ingress_port_vlan(port, False, 0, vlan_id)
        self.add_bridging_entry(vlan_id, zero_mac_addr, zero_mac_addr, next_id)
        self.add_forwarding_acl_cpu_entry(eth_type=ETH_TYPE_ARP, clone=True)
        self.add_next_multicast(next_id, mcast_group_id)
        # FIXME: use clone session APIs when supported on PI
        # For now we add the CPU port to the mc group.
        self.add_mcast_group(mcast_group_id, all_ports + [self.cpu_port])
        for port in untagged_ports:
            self.set_egress_vlan_pop(port, vlan_id)

        for inport in all_ports:
            pkt_to_send = vlan_arp_pkt if inport in tagged_ports else arp_pkt
            testutils.send_packet(self, inport, str(pkt_to_send))
            # Pkt should be received on CPU and on all ports, except the ingress one.
            self.verify_packet_in(exp_pkt=pkt_to_send, exp_in_port=inport)
            verify_tagged_ports = set(tagged_ports)
            verify_tagged_ports.discard(inport)
            for tport in verify_tagged_ports:
                testutils.verify_packet(self, vlan_arp_pkt, tport)
            verify_untagged_ports = set(untagged_ports)
            verify_untagged_ports.discard(inport)
            for uport in verify_untagged_ports:
                testutils.verify_packet(self, arp_pkt, uport)
        testutils.verify_no_other_packets(self)


class IPv4UnicastTest(FabricTest):
    def runIPv4UnicastTest(self, pkt, dst_mac,
                           tagged1=False, tagged2=False, prefix_len=24,
                           exp_pkt=None, bidirectional=True):
        if IP not in pkt or Ether not in pkt:
            self.fail("Cannot do IPv4 test with packet that is not IP")
        vlan1 = VLAN_ID_1
        vlan2 = VLAN_ID_2
        next_id1 = 10
        next_id2 = 20
        src_ipv4 = pkt[IP].src
        dst_ipv4 = pkt[IP].dst
        src_mac = pkt[Ether].src
        switch_mac = pkt[Ether].dst

        self.setup_port(self.port1, vlan1, tagged1)
        self.setup_port(self.port2, vlan2, tagged2)
        self.set_forwarding_type(self.port1, switch_mac, 0x800,
                                 FORWARDING_TYPE_UNICAST_IPV4)
        self.set_forwarding_type(self.port2, switch_mac, 0x800,
                                 FORWARDING_TYPE_UNICAST_IPV4)
        self.add_forwarding_routing_v4_entry(src_ipv4, prefix_len, next_id1)
        self.add_forwarding_routing_v4_entry(dst_ipv4, prefix_len, next_id2)
        self.add_next_hop_L3(next_id1, self.port1, switch_mac, src_mac)
        self.add_next_hop_L3(next_id2, self.port2, switch_mac, dst_mac)
        self.add_vlan_meta(next_id1, vlan1)
        self.add_vlan_meta(next_id2, vlan2)

        if exp_pkt is None:
            exp_pkt = pkt.copy()
            exp_pkt[Ether].src = switch_mac
            exp_pkt[Ether].dst = dst_mac
            exp_pkt[IP].ttl = exp_pkt[IP].ttl - 1
            if tagged2:
                exp_pkt = pkt_add_vlan(exp_pkt, vlan_vid=vlan2)

        pkt2 = pkt.copy()
        pkt2[Ether].src = dst_mac
        pkt2[IP].src = dst_ipv4
        pkt2[IP].dst = src_ipv4

        exp_pkt2 = pkt2.copy()
        exp_pkt2[Ether].src = switch_mac
        exp_pkt2[Ether].dst = src_mac
        exp_pkt2[IP].ttl = exp_pkt2[IP].ttl - 1

        if tagged1:
            pkt = pkt_add_vlan(pkt, vlan_vid=vlan1)
            exp_pkt2 = pkt_add_vlan(exp_pkt2, vlan_vid=vlan1)

        if tagged2:
            pkt2 = pkt_add_vlan(pkt2, vlan_vid=vlan2)

        testutils.send_packet(self, self.port1, str(pkt))
        exp_pkts = [exp_pkt]
        exp_ports = [self.port2]

        if bidirectional:
            testutils.send_packet(self, self.port2, str(pkt2))
            exp_pkts.append(exp_pkt2)
            exp_ports.append(self.port1)

        testutils.verify_each_packet_on_each_port(self, exp_pkts, exp_ports)


class PacketOutTest(FabricTest):
    def runPacketOutTest(self, pkt):
        for port in [self.port1, self.port2]:
            port_hex = stringify(port, 2)
            packet_out = p4runtime_pb2.PacketOut()
            packet_out.payload = str(pkt)
            egress_physical_port = packet_out.metadata.add()
            egress_physical_port.metadata_id = 1
            egress_physical_port.value = port_hex

            self.send_packet_out(packet_out)
            testutils.verify_packet(self, pkt, port)
        testutils.verify_no_other_packets(self)


class PacketInTest(FabricTest):
    def runPacketInTest(self, pkt, eth_type, tagged=False, vlan_id=10):
        self.add_forwarding_acl_cpu_entry(eth_type=eth_type)
        for port in [self.port1, self.port2]:
            if tagged:
                self.set_ingress_port_vlan(port, True, vlan_id, vlan_id)
            else:
                self.set_ingress_port_vlan(port, False, 0, vlan_id)
            testutils.send_packet(self, port, str(pkt))
            self.verify_packet_in(pkt, port)
        testutils.verify_no_other_packets(self)


class SpgwTest(FabricTest):
    def setUp(self):
        super(SpgwTest, self).setUp()
        self.SWITCH_MAC_1 = "c2:42:59:2d:3a:84"
        self.SWITCH_MAC_2 = "3a:c1:e2:53:e1:50"
        self.DMAC_1 = "52:54:00:05:7b:59"
        self.DMAC_2 = "52:54:00:29:c9:b7"

        self.set_forwarding_type(self.port1, self.SWITCH_MAC_1, 0x800,
                                 FORWARDING_TYPE_UNICAST_IPV4)
        self.set_forwarding_type(self.port2, self.SWITCH_MAC_2, 0x800,
                                 FORWARDING_TYPE_UNICAST_IPV4)


class SpgwMPLSTest(SpgwTest):
    def setUp(self):
        super(SpgwMPLSTest, self).setUp()

        self.mpls_label = 204

        # internal vlan required for MPLS
        self.set_ingress_port_vlan(self.port1, vlan_valid=False,
                                   vlan_id=0, new_vlan_id=4094)
        self.set_ingress_port_vlan(self.port2, vlan_valid=False,
                                   vlan_id=0, new_vlan_id=20)
        self.add_forwarding_routing_v4_entry(S1U_ENB_IPV4, 32, 1)
        self.add_forwarding_routing_v4_entry(UE_IPV4, 32, 2)
        self.add_next_hop_L3(1, self.port2, self.SWITCH_MAC_2, self.DMAC_2)
        self.add_next_hop_mpls_v4(2, self.port1, self.SWITCH_MAC_1, self.DMAC_1,
                                  self.mpls_label)
        self.set_egress_vlan_pop(self.port1, 20)
        self.set_egress_vlan_pop(self.port2, 4094)

        req = p4runtime_pb2.WriteRequest()
        req.device_id = self.device_id
        s1u_enb_ipv4_ = ipv4_to_binary(S1U_ENB_IPV4)
        s1u_sgw_ipv4_ = ipv4_to_binary(S1U_SGW_IPV4)
        end_point_ipv4_ = ipv4_to_binary(UE_IPV4)
        self.push_update_add_entry_to_action(
            req,
            "spgw_ingress.s1u_filter_table",
            [self.Exact("gtpu_ipv4.dst_addr", s1u_sgw_ipv4_)],
            "NoAction", [])
        self.push_update_add_entry_to_action(
            req,
            "spgw_ingress.dl_sess_lookup",
            [self.Exact("ipv4.dst_addr", end_point_ipv4_)],
            "spgw_ingress.set_dl_sess_info",
            [("teid", stringify(1, 4)), ("s1u_enb_addr", s1u_enb_ipv4_),
             ("s1u_sgw_addr", s1u_sgw_ipv4_)])
        self.write_request(req)


class SpgwSimpleTest(SpgwTest):
    def setUp(self):
        super(SpgwSimpleTest, self).setUp()

        vlan_id = 10
        self.set_ingress_port_vlan(self.port1, False, 0, vlan_id)
        self.set_ingress_port_vlan(self.port2, False, 0, vlan_id)

        self.add_forwarding_routing_v4_entry(S1U_ENB_IPV4, 32, 1)
        self.add_forwarding_routing_v4_entry(UE_IPV4, 32, 2)
        self.add_next_hop_L3(1, self.port2, self.SWITCH_MAC_2, self.DMAC_2)
        self.add_next_hop_L3(2, self.port1, self.SWITCH_MAC_1, self.DMAC_1)

        self.set_egress_vlan_pop(self.port1, vlan_id)
        self.set_egress_vlan_pop(self.port2, vlan_id)

        req = p4runtime_pb2.WriteRequest()
        req.device_id = self.device_id
        s1u_enb_ipv4_ = ipv4_to_binary(S1U_ENB_IPV4)
        s1u_sgw_ipv4_ = ipv4_to_binary(S1U_SGW_IPV4)
        end_point_ipv4_ = ipv4_to_binary(UE_IPV4)
        self.push_update_add_entry_to_action(
            req,
            "spgw_ingress.s1u_filter_table",
            [self.Exact("gtpu_ipv4.dst_addr", s1u_sgw_ipv4_)],
            "NoAction", [])
        self.push_update_add_entry_to_action(
            req,
            "spgw_ingress.dl_sess_lookup",
            [self.Exact("ipv4.dst_addr", end_point_ipv4_)],
            "spgw_ingress.set_dl_sess_info",
            [("teid", stringify(1, 4)), ("s1u_enb_addr", s1u_enb_ipv4_),
             ("s1u_sgw_addr", s1u_sgw_ipv4_)])
        self.write_request(req)