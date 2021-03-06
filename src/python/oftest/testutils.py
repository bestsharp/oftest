import sys
import copy
import logging
import types
import time
import re

try:
    import scapy.all as scapy
except:
    try:
        import scapy as scapy
    except:
        sys.exit("Need to install scapy for packet parsing")

import oftest
import oftest.controller
import oftest.dataplane
import of10.cstruct
import of10.message
import of10.action
import of10.parse

global skipped_test_count
skipped_test_count = 0

_import_blacklist = set(locals().keys())

# Some useful defines
IP_ETHERTYPE = 0x800
TCP_PROTOCOL = 0x6
UDP_PROTOCOL = 0x11

MINSIZE = 0

def delete_all_flows(ctrl):
    """
    Delete all flows on the switch
    @param ctrl The controller object for the test
    """

    logging.info("Deleting all flows")
    msg = of10.message.flow_mod()
    msg.match.wildcards = of10.cstruct.OFPFW_ALL
    msg.out_port = of10.cstruct.OFPP_NONE
    msg.command = of10.cstruct.OFPFC_DELETE
    msg.buffer_id = 0xffffffff
    ctrl.message_send(msg)
    return 0 # for backwards compatibility

def required_wildcards(parent):
    w = test_param_get('required_wildcards', default='default')
    if w == 'l3-l4':
        return (of10.cstruct.OFPFW_NW_SRC_ALL | of10.cstruct.OFPFW_NW_DST_ALL | of10.cstruct.OFPFW_NW_TOS
                | of10.cstruct.OFPFW_NW_PROTO | of10.cstruct.OFPFW_TP_SRC | of10.cstruct.OFPFW_TP_DST)
    else:
        return 0

def simple_tcp_packet(pktlen=100, 
                      dl_dst='00:01:02:03:04:05',
                      dl_src='00:06:07:08:09:0a',
                      dl_vlan_enable=False,
                      dl_vlan=0,
                      dl_vlan_pcp=0,
                      dl_vlan_cfi=0,
                      ip_src='192.168.0.1',
                      ip_dst='192.168.0.2',
                      ip_tos=0,
                      tcp_sport=1234,
                      tcp_dport=80,
                      ip_ihl=None,
                      ip_options=False
                      ):
    """
    Return a simple dataplane TCP packet

    Supports a few parameters:
    @param len Length of packet in bytes w/o CRC
    @param dl_dst Destinatino MAC
    @param dl_src Source MAC
    @param dl_vlan_enable True if the packet is with vlan, False otherwise
    @param dl_vlan VLAN ID
    @param dl_vlan_pcp VLAN priority
    @param ip_src IP source
    @param ip_dst IP destination
    @param ip_tos IP ToS
    @param tcp_dport TCP destination port
    @param ip_sport TCP source port

    Generates a simple TCP request.  Users
    shouldn't assume anything about this packet other than that
    it is a valid ethernet/IP/TCP frame.
    """

    if MINSIZE > pktlen:
        pktlen = MINSIZE

    # Note Dot1Q.id is really CFI
    if (dl_vlan_enable):
        pkt = scapy.Ether(dst=dl_dst, src=dl_src)/ \
            scapy.Dot1Q(prio=dl_vlan_pcp, id=dl_vlan_cfi, vlan=dl_vlan)/ \
            scapy.IP(src=ip_src, dst=ip_dst, tos=ip_tos, ihl=ip_ihl)/ \
            scapy.TCP(sport=tcp_sport, dport=tcp_dport)
    else:
        if not ip_options:
            pkt = scapy.Ether(dst=dl_dst, src=dl_src)/ \
                scapy.IP(src=ip_src, dst=ip_dst, tos=ip_tos, ihl=ip_ihl)/ \
                scapy.TCP(sport=tcp_sport, dport=tcp_dport)
        else:
            pkt = scapy.Ether(dst=dl_dst, src=dl_src)/ \
                scapy.IP(src=ip_src, dst=ip_dst, tos=ip_tos, ihl=ip_ihl, options=ip_options)/ \
                scapy.TCP(sport=tcp_sport, dport=tcp_dport)

    pkt = pkt/("D" * (pktlen - len(pkt)))

    return pkt

def simple_udp_packet(pktlen=100,
                      dl_dst='00:01:02:03:04:05',
                      dl_src='00:06:07:08:09:0a',
                      dl_vlan_enable=False,
                      dl_vlan=0,
                      dl_vlan_pcp=0,
                      dl_vlan_cfi=0,
                      ip_src='192.168.0.1',
                      ip_dst='192.168.0.2',
                      ip_tos=0,
                      udp_sport=1234,
                      udp_dport=80,
                      ip_ihl=None,
                      ip_options=False
                      ):
    """
    Return a simple dataplane UDP packet

    Supports a few parameters:
    @param len Length of packet in bytes w/o CRC
    @param dl_dst Destination MAC
    @param dl_src Source MAC
    @param dl_vlan_enable True if the packet is with vlan, False otherwise
    @param dl_vlan VLAN ID
    @param dl_vlan_pcp VLAN priority
    @param ip_src IP source
    @param ip_dst IP destination
    @param ip_tos IP ToS
    @param udp_dport UDP destination port
    @param udp_sport UDP source port

    Generates a simple UDP packet. Users shouldn't assume anything about
    this packet other than that it is a valid ethernet/IP/UDP frame.
    """

    if MINSIZE > pktlen:
        pktlen = MINSIZE

    # Note Dot1Q.id is really CFI
    if (dl_vlan_enable):
        pkt = scapy.Ether(dst=dl_dst, src=dl_src)/ \
            scapy.Dot1Q(prio=dl_vlan_pcp, id=dl_vlan_cfi, vlan=dl_vlan)/ \
            scapy.IP(src=ip_src, dst=ip_dst, tos=ip_tos, ihl=ip_ihl)/ \
            scapy.UDP(sport=udp_sport, dport=udp_dport)
    else:
        if not ip_options:
            pkt = scapy.Ether(dst=dl_dst, src=dl_src)/ \
                scapy.IP(src=ip_src, dst=ip_dst, tos=ip_tos, ihl=ip_ihl)/ \
                scapy.UDP(sport=udp_sport, dport=udp_dport)
        else:
            pkt = scapy.Ether(dst=dl_dst, src=dl_src)/ \
                scapy.IP(src=ip_src, dst=ip_dst, tos=ip_tos, ihl=ip_ihl, options=ip_options)/ \
                scapy.UDP(sport=udp_sport, dport=udp_dport)

    pkt = pkt/("D" * (pktlen - len(pkt)))

    return pkt

def simple_icmp_packet(pktlen=60, 
                      dl_dst='00:01:02:03:04:05',
                      dl_src='00:06:07:08:09:0a',
                      dl_vlan_enable=False,
                      dl_vlan=0,
                      dl_vlan_pcp=0,
                      ip_src='192.168.0.1',
                      ip_dst='192.168.0.2',
                      ip_tos=0,
                      icmp_type=8,
                      icmp_code=0
                      ):
    """
    Return a simple ICMP packet

    Supports a few parameters:
    @param len Length of packet in bytes w/o CRC
    @param dl_dst Destinatino MAC
    @param dl_src Source MAC
    @param dl_vlan_enable True if the packet is with vlan, False otherwise
    @param dl_vlan VLAN ID
    @param dl_vlan_pcp VLAN priority
    @param ip_src IP source
    @param ip_dst IP destination
    @param ip_tos IP ToS
    @param icmp_type ICMP type
    @param icmp_code ICMP code

    Generates a simple ICMP ECHO REQUEST.  Users
    shouldn't assume anything about this packet other than that
    it is a valid ethernet/ICMP frame.
    """

    if MINSIZE > pktlen:
        pktlen = MINSIZE

    if (dl_vlan_enable):
        pkt = scapy.Ether(dst=dl_dst, src=dl_src)/ \
            scapy.Dot1Q(prio=dl_vlan_pcp, id=0, vlan=dl_vlan)/ \
            scapy.IP(src=ip_src, dst=ip_dst, tos=ip_tos)/ \
            scapy.ICMP(type=icmp_type, code=icmp_code)
    else:
        pkt = scapy.Ether(dst=dl_dst, src=dl_src)/ \
            scapy.IP(src=ip_src, dst=ip_dst, tos=ip_tos)/ \
            scapy.ICMP(type=icmp_type, code=icmp_code)

    pkt = pkt/("0" * (pktlen - len(pkt)))

    return pkt

def simple_eth_packet(pktlen=60,
                      dl_dst='00:01:02:03:04:05',
                      dl_src='01:80:c2:00:00:00',
                      dl_type=0x88cc):

    if MINSIZE > pktlen:
        pktlen = MINSIZE

    pkt = scapy.Ether(dst=dl_dst, src=dl_src, type=dl_type)

    pkt = pkt/("0" * (pktlen - len(pkt)))

    return pkt

def qinq_tcp_packet(pktlen=100, 
                    dl_dst='00:01:02:03:04:05',
                    dl_src='00:06:07:08:09:0a',
                    dl_vlan_outer=20,
                    dl_vlan_pcp_outer=0,
                    dl_vlan_cfi_outer=0,
                    dl_vlan=10,
                    dl_vlan_pcp=0,
                    dl_vlan_cfi=0,
                    ip_src='192.168.0.1',
                    ip_dst='192.168.0.2',
                    ip_tos=0,
                    tcp_sport=1234,
                    tcp_dport=80,
                    ip_ihl=None,
                    ip_options=False
                    ):
    """
    Return a doubly tagged dataplane TCP packet

    Supports a few parameters:
    @param len Length of packet in bytes w/o CRC
    @param dl_dst Destinatino MAC
    @param dl_src Source MAC
    @param dl_vlan_outer Outer VLAN ID
    @param dl_vlan_pcp_outer Outer VLAN priority
    @param dl_vlan_cfi_outer Outer VLAN cfi bit
    @param dl_vlan Inner VLAN ID
    @param dl_vlan_pcp VLAN priority
    @param dl_vlan_cfi VLAN cfi bit
    @param ip_src IP source
    @param ip_dst IP destination
    @param ip_tos IP ToS
    @param tcp_dport TCP destination port
    @param ip_sport TCP source port

    Generates a TCP request.  Users
    shouldn't assume anything about this packet other than that
    it is a valid ethernet/IP/TCP frame.
    """

    if MINSIZE > pktlen:
        pktlen = MINSIZE

    # Note Dot1Q.id is really CFI
    pkt = scapy.Ether(dst=dl_dst, src=dl_src)/ \
          scapy.Dot1Q(prio=dl_vlan_pcp_outer, id=dl_vlan_cfi_outer, vlan=dl_vlan_outer)/ \
          scapy.Dot1Q(prio=dl_vlan_pcp, id=dl_vlan_cfi, vlan=dl_vlan)/ \
          scapy.IP(src=ip_src, dst=ip_dst, tos=ip_tos, ihl=ip_ihl)/ \
          scapy.TCP(sport=tcp_sport, dport=tcp_dport)

    pkt = pkt/("D" * (pktlen - len(pkt)))

    return pkt

def do_barrier(ctrl, timeout=-1):
    """
    Do a barrier command
    Return 0 on success, -1 on error
    """
    b = of10.message.barrier_request()
    (resp, pkt) = ctrl.transact(b, timeout=timeout)
    if resp is None:
        raise AssertionError("barrier failed")
    # We'll trust the transaction processing in the controller that xid matched
    return 0 # for backwards compatibility

def port_config_get(controller, port_no):
    """
    Get a port's configuration

    Gets the switch feature configuration and grabs one port's
    configuration

    @returns (hwaddr, config, advert) The hwaddress, configuration and
    advertised values
    """
    request = of10.message.features_request()
    reply, pkt = controller.transact(request)
    logging.debug(reply.show())
    if reply is None:
        logging.warn("Get feature request failed")
        return None, None, None
    for idx in range(len(reply.ports)):
        if reply.ports[idx].port_no == port_no:
            return (reply.ports[idx].hw_addr, reply.ports[idx].config,
                    reply.ports[idx].advertised)
    
    logging.warn("Did not find port number for port config")
    return None, None, None

def port_config_set(controller, port_no, config, mask):
    """
    Set the port configuration according the given parameters

    Gets the switch feature configuration and updates one port's
    configuration value according to config and mask
    """
    logging.info("Setting port " + str(port_no) + " to config " + str(config))
    request = of10.message.features_request()
    reply, pkt = controller.transact(request)
    if reply is None:
        return -1
    logging.debug(reply.show())
    p = None
    for idx in range(len(reply.ports)):
        if reply.ports[idx].port_no == port_no:
            p = reply.ports[idx]
            break
    mod = of10.message.port_mod()
    mod.port_no = port_no
    if p:
        mod.hw_addr = p.hw_addr
    mod.config = config
    mod.mask = mask
    if p:
        mod.advertise = p.advertised
    controller.message_send(mod)
    return 0

def receive_pkt_check(dp, pkt, yes_ports, no_ports, assert_if):
    """
    Check for proper receive packets across all ports
    @param dp The dataplane object
    @param pkt Expected packet; may be None if yes_ports is empty
    @param yes_ports Set or list of ports that should recieve packet
    @param no_ports Set or list of ports that should not receive packet
    @param assert_if Object that implements assertXXX
    """

    # Wait this long for packets that we don't expect to receive.
    # 100ms is (rarely) too short for positive tests on slow
    # switches but is definitely not too short for a negative test.
    negative_timeout = 0.1

    exp_pkt_arg = None
    if oftest.config["relax"]:
        exp_pkt_arg = pkt

    for ofport in yes_ports:
        logging.debug("Checking for pkt on port " + str(ofport))
        (rcv_port, rcv_pkt, pkt_time) = dp.poll(
            port_number=ofport, exp_pkt=exp_pkt_arg)
        assert_if.assertTrue(rcv_pkt is not None, 
                             "Did not receive pkt on " + str(ofport))
        if not oftest.dataplane.match_exp_pkt(pkt, rcv_pkt):
            logging.debug("Sent %s" % format_packet(pkt))
            logging.debug("Resp %s" % format_packet(rcv_pkt))
        assert_if.assertTrue(oftest.dataplane.match_exp_pkt(pkt, rcv_pkt),
                             "Response packet does not match send packet " +
                             "on port " + str(ofport))
    if len(no_ports) > 0:
        time.sleep(negative_timeout)
    for ofport in no_ports:
        logging.debug("Negative check for pkt on port " + str(ofport))
        (rcv_port, rcv_pkt, pkt_time) = dp.poll(
            port_number=ofport, timeout=0, exp_pkt=exp_pkt_arg)
        assert_if.assertTrue(rcv_pkt is None, 
                             "Unexpected pkt on port " + str(ofport))


def receive_pkt_verify(parent, egr_ports, exp_pkt, ing_port):
    """
    Receive a packet and verify it matches an expected value
    @param egr_port A single port or list of ports

    parent must implement dataplane, assertTrue and assertEqual
    """
    exp_pkt_arg = None
    if oftest.config["relax"]:
        exp_pkt_arg = exp_pkt

    if type(egr_ports) == type([]):
        egr_port_list = egr_ports
    else:
        egr_port_list = [egr_ports]

    # Expect a packet from each port on egr port list
    for egr_port in egr_port_list:
        check_port = egr_port
        if egr_port == of10.cstruct.OFPP_IN_PORT:
            check_port = ing_port
        (rcv_port, rcv_pkt, pkt_time) = parent.dataplane.poll(
            port_number=check_port, exp_pkt=exp_pkt_arg)

        if rcv_pkt is None:
            logging.error("ERROR: No packet received from " + 
                                str(check_port))

        parent.assertTrue(rcv_pkt is not None,
                          "Did not receive packet port " + str(check_port))
        logging.debug("Packet len " + str(len(rcv_pkt)) + " in on " + 
                            str(rcv_port))

        if str(exp_pkt) != str(rcv_pkt):
            logging.error("ERROR: Packet match failed.")
            logging.debug("Expected len " + str(len(exp_pkt)) + ": "
                                + str(exp_pkt).encode('hex'))
            logging.debug("Received len " + str(len(rcv_pkt)) + ": "
                                + str(rcv_pkt).encode('hex'))
            logging.debug("Expected packet: " + inspect_packet(scapy.Ether(str(exp_pkt))))
            logging.debug("Received packet: " + inspect_packet(scapy.Ether(str(rcv_pkt))))
        parent.assertEqual(str(exp_pkt), str(rcv_pkt),
                           "Packet match error on port " + str(check_port))

def match_verify(parent, req_match, res_match):
    """
    Verify flow matches agree; if they disagree, report where

    parent must implement assertEqual
    Use str() to ensure content is compared and not pointers
    """

    parent.assertEqual(req_match.wildcards, res_match.wildcards,
                       'Match failed: wildcards: ' + hex(req_match.wildcards) +
                       " != " + hex(res_match.wildcards))
    parent.assertEqual(req_match.in_port, res_match.in_port,
                       'Match failed: in_port: ' + str(req_match.in_port) +
                       " != " + str(res_match.in_port))
    parent.assertEqual(str(req_match.dl_src), str(res_match.dl_src),
                       'Match failed: dl_src: ' + str(req_match.dl_src) +
                       " != " + str(res_match.dl_src))
    parent.assertEqual(str(req_match.dl_dst), str(res_match.dl_dst),
                       'Match failed: dl_dst: ' + str(req_match.dl_dst) +
                       " != " + str(res_match.dl_dst))
    parent.assertEqual(req_match.dl_vlan, res_match.dl_vlan,
                       'Match failed: dl_vlan: ' + str(req_match.dl_vlan) +
                       " != " + str(res_match.dl_vlan))
    parent.assertEqual(req_match.dl_vlan_pcp, res_match.dl_vlan_pcp,
                       'Match failed: dl_vlan_pcp: ' + 
                       str(req_match.dl_vlan_pcp) + " != " + 
                       str(res_match.dl_vlan_pcp))
    parent.assertEqual(req_match.dl_type, res_match.dl_type,
                       'Match failed: dl_type: ' + str(req_match.dl_type) +
                       " != " + str(res_match.dl_type))

    if (not(req_match.wildcards & of10.cstruct.OFPFW_DL_TYPE)
        and (req_match.dl_type == IP_ETHERTYPE)):
        parent.assertEqual(req_match.nw_tos, res_match.nw_tos,
                           'Match failed: nw_tos: ' + str(req_match.nw_tos) +
                           " != " + str(res_match.nw_tos))
        parent.assertEqual(req_match.nw_proto, res_match.nw_proto,
                           'Match failed: nw_proto: ' + str(req_match.nw_proto) +
                           " != " + str(res_match.nw_proto))
        parent.assertEqual(req_match.nw_src, res_match.nw_src,
                           'Match failed: nw_src: ' + str(req_match.nw_src) +
                           " != " + str(res_match.nw_src))
        parent.assertEqual(req_match.nw_dst, res_match.nw_dst,
                           'Match failed: nw_dst: ' + str(req_match.nw_dst) +
                           " != " + str(res_match.nw_dst))

        if (not(req_match.wildcards & of10.cstruct.OFPFW_NW_PROTO)
            and ((req_match.nw_proto == TCP_PROTOCOL)
                 or (req_match.nw_proto == UDP_PROTOCOL))):
            parent.assertEqual(req_match.tp_src, res_match.tp_src,
                               'Match failed: tp_src: ' + 
                               str(req_match.tp_src) +
                               " != " + str(res_match.tp_src))
            parent.assertEqual(req_match.tp_dst, res_match.tp_dst,
                               'Match failed: tp_dst: ' + 
                               str(req_match.tp_dst) +
                               " != " + str(res_match.tp_dst))

def packet_to_flow_match(parent, packet):
    match = of10.parse.packet_to_flow_match(packet)
    match.wildcards |= required_wildcards(parent)
    return match

def flow_msg_create(parent, pkt, ing_port=None, action_list=None, wildcards=None,
               egr_ports=None, egr_queue=None, check_expire=False, in_band=False):
    """
    Create a flow message

    Match on packet with given wildcards.  
    See flow_match_test for other parameter descriptoins
    @param egr_queue if not None, make the output an enqueue action
    @param in_band if True, do not wildcard ingress port
    @param egr_ports None (drop), single port or list of ports
    """
    match = of10.parse.packet_to_flow_match(pkt)
    parent.assertTrue(match is not None, "Flow match from pkt failed")
    if wildcards is None:
        wildcards = required_wildcards(parent)
    if in_band:
        wildcards &= ~of10.cstruct.OFPFW_IN_PORT
    match.wildcards = wildcards
    match.in_port = ing_port

    if type(egr_ports) == type([]):
        egr_port_list = egr_ports
    else:
        egr_port_list = [egr_ports]

    request = of10.message.flow_mod()
    request.match = match
    request.buffer_id = 0xffffffff
    if check_expire:
        request.flags |= of10.cstruct.OFPFF_SEND_FLOW_REM
        request.hard_timeout = 1

    if action_list is not None:
        for act in action_list:
            logging.debug("Adding action " + act.show())
            request.actions.add(act)

    # Set up output/enqueue action if directed
    if egr_queue is not None:
        parent.assertTrue(egr_ports is not None, "Egress port not set")
        act = of10.action.action_enqueue()
        for egr_port in egr_port_list:
            act.port = egr_port
            act.queue_id = egr_queue
            request.actions.add(act)
    elif egr_ports is not None:
        for egr_port in egr_port_list:
            act = of10.action.action_output()
            act.port = egr_port
            request.actions.add(act)

    logging.debug(request.show())

    return request

def flow_msg_install(parent, request, clear_table_override=None):
    """
    Install a flow mod message in the switch

    @param parent Must implement controller, assertEqual, assertTrue
    @param request The request, all set to go
    @param clear_table If true, clear the flow table before installing
    """

    clear_table = test_param_get('clear_table', default=True)
    if(clear_table_override != None):
        clear_table = clear_table_override

    if clear_table: 
        logging.debug("Clear flow table")
        delete_all_flows(parent.controller)

    logging.debug("Insert flow")
    parent.controller.message_send(request)

    do_barrier(parent.controller)

def flow_match_test_port_pair(parent, ing_port, egr_ports, wildcards=None,
                              dl_vlan=-1, pkt=None, exp_pkt=None,
                              action_list=None):
    """
    Flow match test on single TCP packet
    @param egr_ports A single port or list of ports

    Run test with packet through switch from ing_port to egr_port
    See flow_match_test for parameter descriptions
    """

    if wildcards is None:
        wildcards = required_wildcards(parent)
    logging.info("Pkt match test: " + str(ing_port) + " to " + 
                       str(egr_ports))
    logging.debug("  WC: " + hex(wildcards) + " vlan: " + str(dl_vlan))
    if pkt is None:
        pkt = simple_tcp_packet(dl_vlan_enable=(dl_vlan >= 0), dl_vlan=dl_vlan)

    request = flow_msg_create(parent, pkt, ing_port=ing_port, 
                              wildcards=wildcards, egr_ports=egr_ports,
                              action_list=action_list)

    flow_msg_install(parent, request)

    logging.debug("Send packet: " + str(ing_port) + " to " + 
                        str(egr_ports))
    parent.dataplane.send(ing_port, str(pkt))

    if exp_pkt is None:
        exp_pkt = pkt
    receive_pkt_verify(parent, egr_ports, exp_pkt, ing_port)

def flow_match_test_pktout(parent, ing_port, egr_ports,
                           dl_vlan=-1, pkt=None, exp_pkt=None,
                           action_list=None):
    """
    Packet-out test on single TCP packet
    @param egr_ports A single port or list of ports

    Run test sending packet-out to egr_ports. The goal is to test the actions
    taken on the packet, not the matching which is of course irrelevant.
    See flow_match_test for parameter descriptions
    """

    if pkt is None:
        pkt = simple_tcp_packet(dl_vlan_enable=(dl_vlan >= 0), dl_vlan=dl_vlan)

    msg = of10.message.packet_out()
    msg.in_port = ing_port
    msg.data = str(pkt)
    if action_list is not None:
        for act in action_list:
            msg.actions.add(act)

    # Set up output action
    if egr_ports is not None:
        for egr_port in egr_ports:
            act = of10.action.action_output()
            act.port = egr_port
            msg.actions.add(act)

    logging.debug(msg.show())
    parent.controller.message_send(msg)

    if exp_pkt is None:
        exp_pkt = pkt
    receive_pkt_verify(parent, egr_ports, exp_pkt, ing_port)

def get_egr_list(parent, of_ports, how_many, exclude_list=[]):
    """
    Generate a list of ports avoiding those in the exclude list
    @param parent Supplies logging
    @param of_ports List of OF port numbers
    @param how_many Number of ports to be added to the list
    @param exclude_list List of ports not to be used
    @returns An empty list if unable to find enough ports
    """

    if how_many == 0:
        return []

    count = 0
    egr_ports = []
    for egr_idx in range(len(of_ports)): 
        if of_ports[egr_idx] not in exclude_list:
            egr_ports.append(of_ports[egr_idx])
            count += 1
            if count >= how_many:
                return egr_ports
    logging.debug("Could not generate enough egress ports for test")
    return []
    
def flow_match_test(parent, port_map, wildcards=None, dl_vlan=-1, pkt=None, 
                    exp_pkt=None, action_list=None,
                    max_test=0, egr_count=1, ing_port=False):
    """
    Run flow_match_test_port_pair on all port pairs and packet-out

    @param max_test If > 0 no more than this number of tests are executed.
    @param parent Must implement controller, dataplane, assertTrue, assertEqual
    and logging
    @param pkt If not None, use this packet for ingress
    @param wildcards For flow match entry
    @param dl_vlan If not -1, and pkt is None, create a pkt w/ VLAN tag
    @param exp_pkt If not None, use this as the expected output pkt; els use pkt
    @param action_list Additional actions to add to flow mod
    @param egr_count Number of egress ports; -1 means get from config w/ dflt 2
    """
    if wildcards is None:
        wildcards = required_wildcards(parent)
    of_ports = port_map.keys()
    of_ports.sort()
    parent.assertTrue(len(of_ports) > 1, "Not enough ports for test")
    test_count = 0

    if egr_count == -1:
        egr_count = test_param_get('egr_count', default=2)
    
    for ing_idx in range(len(of_ports)):
        ingress_port = of_ports[ing_idx]
        egr_ports = get_egr_list(parent, of_ports, egr_count, 
                                 exclude_list=[ingress_port])
        if ing_port:
            egr_ports.append(of10.cstruct.OFPP_IN_PORT)
        if len(egr_ports) == 0:
            parent.assertTrue(0, "Failed to generate egress port list")

        flow_match_test_port_pair(parent, ingress_port, egr_ports, 
                                  wildcards=wildcards, dl_vlan=dl_vlan, 
                                  pkt=pkt, exp_pkt=exp_pkt,
                                  action_list=action_list)
        test_count += 1
        if (max_test > 0) and (test_count > max_test):
            logging.info("Ran " + str(test_count) + " tests; exiting")
            break

    if not test_param_get('pktout_actions', default=True):
        return

    ingress_port = of_ports[0]
    egr_ports = get_egr_list(parent, of_ports, egr_count,
                             exclude_list=[ingress_port])
    if ing_port:
        egr_ports.append(of10.cstruct.OFPP_IN_PORT)
    flow_match_test_pktout(parent, ingress_port, egr_ports,
                           dl_vlan=dl_vlan,
                           pkt=pkt, exp_pkt=exp_pkt,
                           action_list=action_list)

def test_param_get(key, default=None):
    """
    Return value passed via test-params if present

    @param key The lookup key
    @param default Default value to use if not found

    If the pair 'key=val' appeared in the string passed to --test-params
    on the command line, return val (as interpreted by exec).  Otherwise
    return default value.

    WARNING: TEST PARAMETERS MUST BE PYTHON IDENTIFIERS; 
    eg egr_count, not egr-count.
    """
    try:
        exec oftest.config["test_params"]
    except:
        return default

    s = "val = " + str(key)
    try:
        exec s
        return val
    except:
        return default

def action_generate(parent, field_to_mod, mod_field_vals):
    """
    Create an action to modify the field indicated in field_to_mod

    @param parent Must implement, assertTrue
    @param field_to_mod The field to modify as a string name
    @param mod_field_vals Hash of values to use for modified values
    """

    act = None

    if field_to_mod in ['pktlen']:
        return None

    if field_to_mod == 'dl_dst':
        act = of10.action.action_set_dl_dst()
        act.dl_addr = of10.parse.parse_mac(mod_field_vals['dl_dst'])
    elif field_to_mod == 'dl_src':
        act = of10.action.action_set_dl_src()
        act.dl_addr = of10.parse.parse_mac(mod_field_vals['dl_src'])
    elif field_to_mod == 'dl_vlan_enable':
        if not mod_field_vals['dl_vlan_enable']: # Strip VLAN tag
            act = of10.action.action_strip_vlan()
        # Add VLAN tag is handled by dl_vlan field
        # Will return None in this case
    elif field_to_mod == 'dl_vlan':
        act = of10.action.action_set_vlan_vid()
        act.vlan_vid = mod_field_vals['dl_vlan']
    elif field_to_mod == 'dl_vlan_pcp':
        act = of10.action.action_set_vlan_pcp()
        act.vlan_pcp = mod_field_vals['dl_vlan_pcp']
    elif field_to_mod == 'ip_src':
        act = of10.action.action_set_nw_src()
        act.nw_addr = of10.parse.parse_ip(mod_field_vals['ip_src'])
    elif field_to_mod == 'ip_dst':
        act = of10.action.action_set_nw_dst()
        act.nw_addr = of10.parse.parse_ip(mod_field_vals['ip_dst'])
    elif field_to_mod == 'ip_tos':
        act = of10.action.action_set_nw_tos()
        act.nw_tos = mod_field_vals['ip_tos']
    elif field_to_mod == 'tcp_sport':
        act = of10.action.action_set_tp_src()
        act.tp_port = mod_field_vals['tcp_sport']
    elif field_to_mod == 'tcp_dport':
        act = of10.action.action_set_tp_dst()
        act.tp_port = mod_field_vals['tcp_dport']
    elif field_to_mod == 'udp_sport':
        act = of10.action.action_set_tp_src()
        act.tp_port = mod_field_vals['udp_sport']
    elif field_to_mod == 'udp_dport':
        act = of10.action.action_set_tp_dst()
        act.tp_port = mod_field_vals['udp_dport']
    else:
        parent.assertTrue(0, "Unknown field to modify: " + str(field_to_mod))

    return act

def pkt_action_setup(parent, start_field_vals={}, mod_field_vals={}, 
                     mod_fields=[], tp="tcp", check_test_params=False):
    """
    Set up the ingress and expected packet and action list for a test

    @param parent Must implement assertTrue
    @param start_field_values Field values to use for ingress packet (optional)
    @param mod_field_values Field values to use for modified packet (optional)
    @param mod_fields The list of fields to be modified by the switch in the test.
    @params check_test_params If True, will check the parameters vid, add_vlan
    and strip_vlan from the command line.

    Returns a triple:  pkt-to-send, expected-pkt, action-list
    """

    new_actions = []

    base_pkt_params = {}
    base_pkt_params['pktlen'] = 100
    base_pkt_params['dl_dst'] = '00:DE:F0:12:34:56'
    base_pkt_params['dl_src'] = '00:23:45:67:89:AB'
    base_pkt_params['dl_vlan_enable'] = False
    base_pkt_params['dl_vlan'] = 2
    base_pkt_params['dl_vlan_pcp'] = 0
    base_pkt_params['ip_src'] = '192.168.0.1'
    base_pkt_params['ip_dst'] = '192.168.0.2'
    base_pkt_params['ip_tos'] = 0
    if tp == "tcp":
        base_pkt_params['tcp_sport'] = 1234
        base_pkt_params['tcp_dport'] = 80
    elif tp == "udp":
        base_pkt_params['udp_sport'] = 1234
        base_pkt_params['udp_dport'] = 80
    for keyname in start_field_vals.keys():
        base_pkt_params[keyname] = start_field_vals[keyname]

    mod_pkt_params = {}
    mod_pkt_params['pktlen'] = 100
    mod_pkt_params['dl_dst'] = '00:21:0F:ED:CB:A9'
    mod_pkt_params['dl_src'] = '00:ED:CB:A9:87:65'
    mod_pkt_params['dl_vlan_enable'] = False
    mod_pkt_params['dl_vlan'] = 3
    mod_pkt_params['dl_vlan_pcp'] = 7
    mod_pkt_params['ip_src'] = '10.20.30.40'
    mod_pkt_params['ip_dst'] = '50.60.70.80'
    mod_pkt_params['ip_tos'] = 0xf0
    if tp == "tcp":
        mod_pkt_params['tcp_sport'] = 4321
        mod_pkt_params['tcp_dport'] = 8765
    elif tp == "udp":
        mod_pkt_params['udp_sport'] = 4321
        mod_pkt_params['udp_dport'] = 8765
    for keyname in mod_field_vals.keys():
        mod_pkt_params[keyname] = mod_field_vals[keyname]

    # Check for test param modifications
    strip = False
    if check_test_params:
        add_vlan = test_param_get('add_vlan')
        strip_vlan = test_param_get('strip_vlan')
        vid = test_param_get('vid')

        if add_vlan and strip_vlan:
            parent.assertTrue(0, "Add and strip VLAN both specified")

        if vid:
            base_pkt_params['dl_vlan_enable'] = True
            base_pkt_params['dl_vlan'] = vid
            if 'dl_vlan' in mod_fields:
                mod_pkt_params['dl_vlan'] = vid + 1

        if add_vlan:
            base_pkt_params['dl_vlan_enable'] = False
            mod_pkt_params['dl_vlan_enable'] = True
            mod_pkt_params['pktlen'] = base_pkt_params['pktlen'] + 4
            mod_fields.append('pktlen')
            mod_fields.append('dl_vlan_enable')
            if 'dl_vlan' not in mod_fields:
                mod_fields.append('dl_vlan')
        elif strip_vlan:
            base_pkt_params['dl_vlan_enable'] = True
            mod_pkt_params['dl_vlan_enable'] = False
            mod_pkt_params['pktlen'] = base_pkt_params['pktlen'] - 4
            mod_fields.append('dl_vlan_enable')
            mod_fields.append('pktlen')

    if tp == "tcp":
        packet_builder = simple_tcp_packet
    elif tp == "udp":
        packet_builder = simple_udp_packet
    else:
        raise NotImplementedError("unknown transport protocol %s" % tp)

    # Build the ingress packet
    ingress_pkt = packet_builder(**base_pkt_params)

    # Build the expected packet, modifying the indicated fields
    for item in mod_fields:
        base_pkt_params[item] = mod_pkt_params[item]
        act = action_generate(parent, item, mod_pkt_params)
        if act:
            new_actions.append(act)

    expected_pkt = packet_builder(**base_pkt_params)

    return (ingress_pkt, expected_pkt, new_actions)

# Generate a simple "drop" flow mod
# If in_band is true, then only drop from first test port
def flow_mod_gen(port_map, in_band):
    request = of10.message.flow_mod()
    request.match.wildcards = of10.cstruct.OFPFW_ALL
    if in_band:
        request.match.wildcards = of10.cstruct.OFPFW_ALL - of10.cstruct.OFPFW_IN_PORT
        for of_port, ifname in port_map.items(): # Grab first port
            break
        request.match.in_port = of_port
    request.buffer_id = 0xffffffff
    return request

def skip_message_emit(parent, s):
    """
    Print out a 'skipped' message to stderr

    @param s The string to print out to the log file
    """
    global skipped_test_count

    skipped_test_count += 1
    logging.info("Skipping: " + s)
    if oftest.config["dbg_level"] < logging.WARNING:
        sys.stderr.write("(skipped) ")
    else:
        sys.stderr.write("(S)")


def all_stats_get(parent):
    """
    Get the aggregate stats for all flows in the table
    @param parent Test instance with controller connection and assert
    @returns dict with keys flows, packets, bytes, active (flows), 
    lookups, matched
    """
    stat_req = of10.message.aggregate_stats_request()
    stat_req.match = of10.cstruct.ofp_match()
    stat_req.match.wildcards = of10.cstruct.OFPFW_ALL
    stat_req.table_id = 0xff
    stat_req.out_port = of10.cstruct.OFPP_NONE

    rv = {}

    (reply, pkt) = parent.controller.transact(stat_req)
    parent.assertTrue(len(reply.stats) == 1, "Did not receive flow stats reply")

    for obj in reply.stats:
        (rv["flows"], rv["packets"], rv["bytes"]) = (obj.flow_count, 
                                                  obj.packet_count, obj.byte_count)
        break

    request = of10.message.table_stats_request()
    (reply , pkt) = parent.controller.transact(request)

    
    (rv["active"], rv["lookups"], rv["matched"]) = (0,0,0)
    for obj in reply.stats:
        rv["active"] += obj.active_count
        rv["lookups"] += obj.lookup_count
        rv["matched"] += obj.matched_count

    return rv

_import_blacklist.add('FILTER')
FILTER=''.join([(len(repr(chr(x)))==3) and chr(x) or '.' 
                for x in range(256)])

def hex_dump_buffer(src, length=16):
    """
    Convert src to a hex dump string and return the string
    @param src The source buffer
    @param length The number of bytes shown in each line
    @returns A string showing the hex dump
    """
    result = ["\n"]
    for i in xrange(0, len(src), length):
       chars = src[i:i+length]
       hex = ' '.join(["%02x" % ord(x) for x in chars])
       printable = ''.join(["%s" % ((ord(x) <= 127 and
                                     FILTER[ord(x)]) or '.') for x in chars])
       result.append("%04x  %-*s  %s\n" % (i, length*3, hex, printable))
    return ''.join(result)

def format_packet(pkt):
    return "Packet length %d \n%s" % (len(str(pkt)), 
                                      hex_dump_buffer(str(pkt)))

def inspect_packet(pkt):
    """
    Wrapper around scapy's show() method.
    @returns A string showing the dissected packet.
    """
    from cStringIO import StringIO
    out = None
    backup = sys.stdout
    try:
        sys.stdout = StringIO()
        pkt.show2()
        out = sys.stdout.getvalue()
        sys.stdout.close()
    finally:
        sys.stdout = backup
    return out

def nonstandard(cls):
    """
    Testcase decorator that marks the test as being non-standard.
    These tests are not automatically added to the "standard" group.
    """
    cls._nonstandard = True
    return cls

def disabled(cls):
    """
    Testcase decorator that marks the test as being disabled.
    These tests are not automatically added to the "standard" group or
    their module's group.
    """
    cls._disabled = True
    return cls

def group(name):
    """
    Testcase decorator that adds the test to a group.
    """
    def fn(cls):
        if not hasattr(cls, "_groups"):
            cls._groups = []
        cls._groups.append(name)
        return cls
    return fn

def version(ver):
    """
    Testcase decorator that specifies which versions of OpenFlow the test
    supports. The default is 1.0+. This decorator may only be used once.

    Supported syntax:
    1.0 -> 1.0
    1.0,1.2,1.3 -> 1.0, 1.2, 1.3
    1.0+ -> 1.0, 1.1, 1.2, 1.3
    """
    versions = parse_version(ver)
    def fn(cls):
        cls._versions = versions
        return cls
    return fn

def parse_version(ver):
    allowed_versions = ["1.0", "1.1", "1.2", "1.3"]
    if re.match("^1\.\d+$", ver):
        versions = set([ver])
    elif re.match("^(1\.\d+)\+$", ver):
        if not ver[:-1] in allowed_versions:
            raise ValueError("invalid OpenFlow version %s" % ver[:-1])
        versions = set()
        if ver != "1.1+": versions.add("1.0")
        if ver != "1.2+": versions.add("1.1")
        if ver != "1.3+": versions.add("1.2")
        versions.add("1.3")
    else:
        versions = set(ver.split(','))

    for version in versions:
        if not version in allowed_versions:
            raise ValueError("invalid OpenFlow version %s" % version)

    return versions

assert(parse_version("1.0") == set(["1.0"]))
assert(parse_version("1.0,1.2,1.3") == set(["1.0", "1.2", "1.3"]))
assert(parse_version("1.0+") == set(["1.0", "1.1", "1.2", "1.3"]))

__all__ = list(set(locals()) - _import_blacklist)
