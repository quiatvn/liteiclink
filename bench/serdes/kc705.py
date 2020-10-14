#!/usr/bin/env python3

#
# This file is part of LiteICLink.
#
# Copyright (c) 2017-2020 Florent Kermarrec <florent@enjoy-digital.fr>
# SPDX-License-Identifier: BSD-2-Clause

import sys
import argparse

from migen import *
from migen.genlib.io import CRG

from litex.boards.platforms import kc705

from litex.build.generic_platform import *

from litex.soc.integration.soc_core import *
from litex.soc.integration.builder import *

from liteiclink.serdes.gtx_7series import GTXChannelPLL, GTXQuadPLL, GTX

# IOs ----------------------------------------------------------------------------------------------

_transceiver_io = [
    # PCIe
    ("pcie_tx", 0,
        Subsignal("p", Pins("L4")),
        Subsignal("n", Pins("L3"))
    ),
    ("pcie_rx", 0,
        Subsignal("p", Pins("M6")),
        Subsignal("n", Pins("M5"))
    ),
    # SFP: Already provided by KC705 platform.
    # SMA
    ("sma_tx", 0,
        Subsignal("p", Pins("K2")),
        Subsignal("n", Pins("K1"))
    ),
    ("sma_rx", 0,
        Subsignal("p", Pins("K6")),
        Subsignal("n", Pins("K5"))
    ),
]

# GTXTestSoC ---------------------------------------------------------------------------------------

class GTXTestSoC(SoCMini):
    def __init__(self, platform, connector="pcie", linerate=2.5e9, use_qpll=False):
        assert connector in ["pcie", "sfp", "sma"]
        sys_clk_freq = int(156e9)

        # SoCMini ----------------------------------------------------------------------------------
        SoCMini.__init__(self, platform, sys_clk_freq, with_uart=True, uart_name="bridge")

        # CRG --------------------------------------------------------------------------------------
        self.submodules.crg = CRG(platform.request("clk156"), platform.request("cpu_reset"))
        platform.add_period_constraint(self.crg.cd_sys.clk, 1e9/156e6)

        # GTX RefClk -------------------------------------------------------------------------------
        refclk      = Signal()
        refclk_pads = platform.request("sgmii_clock")
        self.specials += Instance("IBUFDS_GTE2",
            i_CEB = 0,
            i_I   = refclk_pads.p,
            i_IB  = refclk_pads.n,
            o_O   = refclk)

        # GTX PLL ----------------------------------------------------------------------------------
        pll_cls = GTXQuadPLL if use_qpll else GTXChannelPLL
        pll     = pll_cls(refclk, 125e6, linerate)
        print(pll)
        self.submodules += pll

        # GTX --------------------------------------------------------------------------------------
        tx_pads = platform.request(connector + "_tx")
        rx_pads = platform.request(connector + "_rx")
        self.submodules.serdes = serdes = GTX(pll, tx_pads, rx_pads, sys_clk_freq,
            data_width       = 40,
            clock_aligner    = False,
            tx_buffer_enable = True,
            rx_buffer_enable = True)
        serdes.add_controls()
        self.add_csr("serdes")

        platform.add_period_constraint(serdes.cd_tx.clk, 1e9/serdes.tx_clk_freq)
        platform.add_period_constraint(serdes.cd_rx.clk, 1e9/serdes.rx_clk_freq)
        self.platform.add_false_path_constraints(self.crg.cd_sys.clk, serdes.cd_tx.clk, serdes.cd_rx.clk)

        if connector == "sfp":
            self.comb += platform.request("sfp_tx_disable_n").eq(1)

        # Test -------------------------------------------------------------------------------------
        counter = Signal(32)
        self.sync.tx += counter.eq(counter + 1)

        # K28.5 and slow counter --> TX
        self.comb += [
            serdes.encoder.k[0].eq(1),
            serdes.encoder.d[0].eq((5 << 5) | 28),
            serdes.encoder.k[1].eq(0),
            serdes.encoder.d[1].eq(counter[26:]),
        ]

        # RX (slow counter) --> Leds
        for i in range(4):
            self.comb += platform.request("user_led", 4 + i).eq(serdes.decoders[1].d[i])

        # Leds -------------------------------------------------------------------------------------
        sys_counter = Signal(32)
        self.sync.sys += sys_counter.eq(sys_counter + 1)
        self.comb += platform.request("user_led", 0).eq(sys_counter[26])

        tx_counter = Signal(32)
        self.sync.tx += tx_counter.eq(tx_counter + 1)
        self.comb += platform.request("user_led", 1).eq(tx_counter[26])

        rx_counter = Signal(32)
        self.sync.rx += rx_counter.eq(rx_counter + 1)
        self.comb += platform.request("user_led", 2).eq(rx_counter[26])

# Build --------------------------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="LiteICLink transceiver example on KC705")
    parser.add_argument("--build",     action="store_true", help="Build bitstream")
    parser.add_argument("--load",      action="store_true", help="Load bitstream (to SRAM)")
    parser.add_argument("--connector", default="pcie",      help="Connector: pcie (default), sfp or sma")
    parser.add_argument("--linerate",  default="2.5e9",     help="Linerate (default: 2.5e9)")
    parser.add_argument("--pll",       default="cpll",      help="PLL: cpll (default) or qpll")
    args = parser.parse_args()

    platform = kc705.Platform()
    platform.add_extension(_transceiver_io)
    soc = GTXTestSoC(platform,
        connector = args.connector,
        linerate  = float(args.linerate),
        use_qpll  = args.pll == "qpll"
    )
    builder = Builder(soc, csr_csv="csr.csv")
    builder.build(run=args.build)

    if args.load:
        prog = soc.platform.create_programmer()
        prog.load_bitstream(os.path.join(builder.gateware_dir, soc.build_name + ".bit"))

if __name__ == "__main__":
    main()
