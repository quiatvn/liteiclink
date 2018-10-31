from migen import *
from migen.genlib.resetsync import AsyncResetSynchronizer

from litex.soc.interconnect.csr import *
from litex.soc.cores.code_8b10b import Encoder, Decoder

from liteiclink.transceiver.gtx_7series_init import GTXInit
from liteiclink.transceiver.clock_aligner import BruteforceClockAligner

from liteiclink.transceiver.prbs import *


class GTXChannelPLL(Module):
    def __init__(self, refclk, refclk_freq, linerate):
        self.refclk = refclk
        self.reset = Signal()
        self.lock = Signal()
        self.config = self.compute_config(refclk_freq, linerate)

    @staticmethod
    def compute_config(refclk_freq, linerate):
        for n1 in 4, 5:
            for n2 in 1, 2, 3, 4, 5:
                for m in 1, 2:
                    vco_freq = refclk_freq*(n1*n2)/m
                    if 1.6e9 <= vco_freq <= 3.3e9:
                        for d in 1, 2, 4, 8, 16:
                            current_linerate = vco_freq*2/d
                            if current_linerate == linerate:
                                return {"n1": n1, "n2": n2, "m": m, "d": d,
                                        "vco_freq": vco_freq,
                                        "clkin": refclk_freq,
                                        "linerate": linerate}
        msg = "No config found for {:3.2f} MHz refclk / {:3.2f} Gbps linerate."
        raise ValueError(msg.format(refclk_freq/1e6, linerate/1e9))

    def __repr__(self):
        r = """
GTXChannelPLL
==============
  overview:
  ---------
       +--------------------------------------------------+
       |                                                  |
       |   +-----+  +---------------------------+ +-----+ |
       |   |     |  | Phase Frequency Detector  | |     | |
CLKIN +----> /M  +-->       Charge Pump         +-> VCO +---> CLKOUT
       |   |     |  |       Loop Filter         | |     | |
       |   +-----+  +---------------------------+ +--+--+ |
       |              ^                              |    |
       |              |    +-------+    +-------+    |    |
       |              +----+  /N2  <----+  /N1  <----+    |
       |                   +-------+    +-------+         |
       +--------------------------------------------------+
                            +-------+
                   CLKOUT +->  2/D  +-> LINERATE
                            +-------+
  config:
  -------
    CLKIN    = {clkin}MHz
    CLKOUT   = CLKIN x (N1 x N2) / M = {clkin}MHz x ({n1} x {n2}) / {m}
             = {vco_freq}GHz
    LINERATE = CLKOUT x 2 / D = {vco_freq}GHz x 2 / {d}
             = {linerate}GHz
""".format(clkin=self.config["clkin"]/1e6,
           n1=self.config["n1"],
           n2=self.config["n2"],
           m=self.config["m"],
           vco_freq=self.config["vco_freq"]/1e9,
           d=self.config["d"],
           linerate=self.config["linerate"]/1e9)
        return r


class GTXQuadPLL(Module):
    def __init__(self, refclk, refclk_freq, linerate):
        self.clk = Signal()
        self.refclk = Signal()
        self.reset = Signal()
        self.lock = Signal()
        self.config = self.compute_config(refclk_freq, linerate)

        # # #

        fbdiv_ratios = {
            16:  1,
            20:  1,
            32:  1,
            40:  1,
            64:  1,
            66:  0,
            80:  1,
            100: 1
        }
        fbdivs = {
            16:  0b0000100000,
            20:  0b0000110000,
            32:  0b0001100000,
            40:  0b0010000000,
            64:  0b0011100000,
            66:  0b0101000000,
            80:  0b0100100000,
            100: 0b0101110000
        }

        self.specials += \
            Instance("GTXE2_COMMON",
                p_QPLL_CFG=0x0680181 if self.config["vco_band"] == "upper" else
                           0x06801c1,
                p_QPLL_FBDIV=fbdivs[self.config["n"]],
                p_QPLL_FBDIV_RATIO=fbdiv_ratios[self.config["n"]],
                p_QPLL_REFCLK_DIV=self.config["m"],
                i_GTREFCLK0=refclk,
                i_QPLLRESET=self.reset,

                o_QPLLOUTCLK=self.clk,
                o_QPLLOUTREFCLK=self.refclk,
                i_QPLLLOCKEN=1,
                o_QPLLLOCK=self.lock,
                i_QPLLREFCLKSEL=0b001
            )

    @staticmethod
    def compute_config(refclk_freq, linerate):
        for n in 16, 20, 32, 40, 64, 66, 80, 100:
            for m in 1, 2, 3, 4:
                vco_freq = refclk_freq*n/m
                if 5.93e9 <= vco_freq <= 8e9:
                    vco_band = "lower"
                elif 9.8e9 <= vco_freq <= 12.5e9:
                    vco_band = "upper"
                else:
                    vco_band = None
                if vco_band is not None:
                    for d in [1, 2, 4, 8, 16]:
                        current_linerate = (vco_freq/2)*2/d
                        if current_linerate == linerate:
                            return {"n": n, "m": m, "d": d,
                                    "vco_freq": vco_freq,
                                    "vco_band": vco_band,
                                    "clkin": refclk_freq,
                                    "clkout": vco_freq/2,
                                    "linerate": linerate}
        msg = "No config found for {:3.2f} MHz refclk / {:3.2f} Gbps linerate."
        raise ValueError(msg.format(refclk_freq/1e6, linerate/1e9))

    def __repr__(self):
        r = """
GTXQuadPLL
===========
  overview:
  ---------
       +-------------------------------------------------------------++
       |                                          +------------+      |
       |   +-----+  +---------------------------+ | Upper Band | +--+ |
       |   |     |  | Phase Frequency Detector  +->    VCO     | |  | |
CLKIN +----> /M  +-->       Charge Pump         | +------------+->/2+--> CLKOUT
       |   |     |  |       Loop Filter         +-> Lower Band | |  | |
       |   +-----+  +---------------------------+ |    VCO     | +--+ |
       |              ^                           +-----+------+      |
       |              |        +-------+                |             |
       |              +--------+  /N   <----------------+             |
       |                       +-------+                              |
       +--------------------------------------------------------------+
                               +-------+
                      CLKOUT +->  2/D  +-> LINERATE
                               +-------+
  config:
  -------
    CLKIN    = {clkin}MHz
    CLKOUT   = CLKIN x N / (2 x M) = {clkin}MHz x {n} / (2 x {m})
             = {clkout}GHz
    VCO      = {vco_freq}GHz ({vco_band})
    LINERATE = CLKOUT x 2 / D = {clkout}GHz x 2 / {d}
             = {linerate}GHz
""".format(clkin=self.config["clkin"]/1e6,
           n=self.config["n"],
           m=self.config["m"],
           clkout=self.config["clkout"]/1e9,
           vco_freq=self.config["vco_freq"]/1e9,
           vco_band=self.config["vco_band"],
           d=self.config["d"],
           linerate=self.config["linerate"]/1e9)
        return r


class GTX(Module, AutoCSR):
    def __init__(self, pll, tx_pads, rx_pads, sys_clk_freq,
                 data_width=20, clock_aligner=True,
                 internal_loopback=False,
                 tx_polarity=0, rx_polarity=0):
        assert (data_width == 20) or (data_width == 40)
        self.tx_produce_square_wave = CSRStorage()
        self.tx_prbs_config = CSRStorage(2)

        self.rx_prbs_config = CSRStorage(2)
        self.rx_prbs_errors = CSRStatus(32)

        # # #

        nwords = data_width//10
        self.submodules.encoder = ClockDomainsRenamer("tx")(
            Encoder(nwords, True))
        self.decoders = [ClockDomainsRenamer("rx")(
            Decoder(True)) for _ in range(nwords)]
        self.submodules += self.decoders

        self.rx_ready = Signal()

        # transceiver direct clock outputs
        # useful to specify clock constraints in a way palatable to Vivado
        self.txoutclk = Signal()
        self.rxoutclk = Signal()

        self.tx_clk_freq = pll.config["linerate"]/20

        # control/status cdc
        tx_produce_square_wave = Signal()
        tx_prbs_config = Signal(2)

        rx_prbs_config = Signal(2)
        rx_prbs_errors = Signal(32)


        self.specials += [
            MultiReg(self.tx_produce_square_wave.storage, tx_produce_square_wave, "tx"),
            MultiReg(self.tx_prbs_config.storage, tx_prbs_config, "tx"),
        ]

        self.specials += [
            MultiReg(self.rx_prbs_config.storage, rx_prbs_config, "rx"),
            MultiReg(rx_prbs_errors, self.rx_prbs_errors.status, "sys"), # FIXME
        ]

        # # #

        use_cpll = isinstance(pll, GTXChannelPLL)
        use_qpll = isinstance(pll, GTXQuadPLL)

        # TX generates TX clock, init must be in system domain
        tx_init = GTXInit(sys_clk_freq, False)
        # RX receives restart commands from TX domain
        rx_init = ClockDomainsRenamer("tx")(
            GTXInit(self.tx_clk_freq, True))
        self.submodules += tx_init, rx_init
        self.comb += [
            tx_init.plllock.eq(pll.lock),
            rx_init.plllock.eq(pll.lock),
            pll.reset.eq(tx_init.pllreset)
        ]

        txdata = Signal(data_width)
        rxdata = Signal(data_width)

        gtx_params = dict(
            # Simulation-Only Attributes
            p_SIM_RECEIVER_DETECT_PASS   ="TRUE",
            p_SIM_TX_EIDLE_DRIVE_LEVEL   ="X",
            p_SIM_RESET_SPEEDUP          ="FALSE",
            p_SIM_CPLLREFCLK_SEL         ="FALSE",
            p_SIM_VERSION                ="4.0",

            # RX Byte and Word Alignment Attributes
            p_ALIGN_COMMA_DOUBLE                     ="FALSE",
            p_ALIGN_COMMA_ENABLE                     =0b1111111111,
            p_ALIGN_COMMA_WORD                       =1,
            p_ALIGN_MCOMMA_DET                       ="TRUE",
            p_ALIGN_MCOMMA_VALUE                     =0b1010000011,
            p_ALIGN_PCOMMA_DET                       ="TRUE",
            p_ALIGN_PCOMMA_VALUE                     =0b0101111100,
            p_SHOW_REALIGN_COMMA                     ="FALSE",
            p_RXSLIDE_AUTO_WAIT                      =7,
            p_RXSLIDE_MODE                           ="PCS",
            p_RX_SIG_VALID_DLY                       =10,

            # RX 8B/10B Decoder Attributes
            p_RX_DISPERR_SEQ_MATCH                   ="FALSE",
            p_DEC_MCOMMA_DETECT                      ="TRUE",
            p_DEC_PCOMMA_DETECT                      ="TRUE",
            p_DEC_VALID_COMMA_ONLY                   ="FALSE",

            # RX Clock Correction Attributes
            p_CBCC_DATA_SOURCE_SEL                   ="ENCODED",
            p_CLK_COR_SEQ_2_USE                      ="FALSE",
            p_CLK_COR_KEEP_IDLE                      ="FALSE",
            p_CLK_COR_MAX_LAT                        =9 if data_width == 16 else 19,
            p_CLK_COR_MIN_LAT                        =7 if data_width == 20 else 15,
            p_CLK_COR_PRECEDENCE                     ="TRUE",
            p_CLK_COR_REPEAT_WAIT                    =0,
            p_CLK_COR_SEQ_LEN                        =1,
            p_CLK_COR_SEQ_1_ENABLE                   =0b1111,
            p_CLK_COR_SEQ_1_1                        =0b0100000000,
            p_CLK_COR_SEQ_1_2                        =0b0000000000,
            p_CLK_COR_SEQ_1_3                        =0b0000000000,
            p_CLK_COR_SEQ_1_4                        =0b0000000000,
            p_CLK_CORRECT_USE                        ="FALSE",
            p_CLK_COR_SEQ_2_ENABLE                   =0b1111,
            p_CLK_COR_SEQ_2_1                        =0b0100000000,
            p_CLK_COR_SEQ_2_2                        =0b0000000000,
            p_CLK_COR_SEQ_2_3                        =0b0000000000,
            p_CLK_COR_SEQ_2_4                        =0b0000000000,

            # RX Channel Bonding Attributes
            p_CHAN_BOND_KEEP_ALIGN                   ="FALSE",
            p_CHAN_BOND_MAX_SKEW                     =1,
            p_CHAN_BOND_SEQ_LEN                      =1,
            p_CHAN_BOND_SEQ_1_1                      =0b0000000000,
            p_CHAN_BOND_SEQ_1_2                      =0b0000000000,
            p_CHAN_BOND_SEQ_1_3                      =0b0000000000,
            p_CHAN_BOND_SEQ_1_4                      =0b0000000000,
            p_CHAN_BOND_SEQ_1_ENABLE                 =0b1111,
            p_CHAN_BOND_SEQ_2_1                      =0b0000000000,
            p_CHAN_BOND_SEQ_2_2                      =0b0000000000,
            p_CHAN_BOND_SEQ_2_3                      =0b0000000000,
            p_CHAN_BOND_SEQ_2_4                      =0b0000000000,
            p_CHAN_BOND_SEQ_2_ENABLE                 =0b1111,
            p_CHAN_BOND_SEQ_2_USE                    ="FALSE",
            p_FTS_DESKEW_SEQ_ENABLE                  =0b1111,
            p_FTS_LANE_DESKEW_CFG                    =0b1111,
            p_FTS_LANE_DESKEW_EN                     ="FALSE",

            # RX Margin Analysis Attributes
            p_ES_CONTROL                             =0b000000,
            p_ES_ERRDET_EN                           ="FALSE",
            p_ES_EYE_SCAN_EN                         ="TRUE",
            p_ES_HORZ_OFFSET                         =0x000,
            p_ES_PMA_CFG                             =0b0000000000,
            p_ES_PRESCALE                            =0b00000,
            p_ES_QUALIFIER                           =0x00000000000000000000,
            p_ES_QUAL_MASK                           =0x00000000000000000000,
            p_ES_SDATA_MASK                          =0x00000000000000000000,
            p_ES_VERT_OFFSET                         =0b000000000,

            # FPGA RX Interface Attributes
            p_RX_DATA_WIDTH                          =data_width,

            # PMA Attributes
            p_OUTREFCLK_SEL_INV                      =0b11,
            p_PMA_RSV                                =0x00018480,
            p_PMA_RSV2                               =0x2050,
            p_PMA_RSV3                               =0b00,
            p_PMA_RSV4                               =0x00000000,
            p_RX_BIAS_CFG                            =0b000000000100,
            p_DMONITOR_CFG                           =0x000A00,
            p_RX_CM_SEL                              =0b11,
            p_RX_CM_TRIM                             =0b010,
            p_RX_DEBUG_CFG                           =0b000000000000,
            p_RX_OS_CFG                              =0b0000010000000,
            p_TERM_RCAL_CFG                          =0b10000,
            p_TERM_RCAL_OVRD                         =0b0,
            p_TST_RSV                                =0x00000000,
            p_RX_CLK25_DIV                           =5,
            p_TX_CLK25_DIV                           =5,
            p_UCODEER_CLR                            =0xb0,

            # PCI Express Attributes
            p_PCS_PCIE_EN                            ="FALSE",

            # PCS Attributes
            p_PCS_RSVD_ATTR                          =0x000000000000,

            # RX Buffer Attributes
            p_RXBUF_ADDR_MODE                        ="FAST",
            p_RXBUF_EIDLE_HI_CNT                     =0b1000,
            p_RXBUF_EIDLE_LO_CNT                     =0b0000,
            p_RXBUF_EN                               ="FALSE",
            p_RX_BUFFER_CFG                          =0b000000,
            p_RXBUF_RESET_ON_CB_CHANGE               ="TRUE",
            p_RXBUF_RESET_ON_COMMAALIGN              ="FALSE",
            p_RXBUF_RESET_ON_EIDLE                   ="FALSE",
            p_RXBUF_RESET_ON_RATE_CHANGE             ="TRUE",
            p_RXBUFRESET_TIME                        =0b00001,
            p_RXBUF_THRESH_OVFLW                     =61,
            p_RXBUF_THRESH_OVRD                      ="FALSE",
            p_RXBUF_THRESH_UNDFLW                    =4,
            p_RXDLY_CFG                              =0x001F,
            p_RXDLY_LCFG                             =0x030,
            p_RXDLY_TAP_CFG                          =0x0000,
            p_RXPH_CFG                               =0x000000,
            p_RXPHDLY_CFG                            =0x084020,
            p_RXPH_MONITOR_SEL                       =0b00000,
            p_RX_XCLK_SEL                            ="RXUSR",
            p_RX_DDI_SEL                             =0b000000,
            p_RX_DEFER_RESET_BUF_EN                  ="TRUE",

            # CDR Attributes
            p_RXCDR_CFG                              =0x03000023ff40100020,
            p_RXCDR_FR_RESET_ON_EIDLE                =0b0,
            p_RXCDR_HOLD_DURING_EIDLE                =0b0,
            p_RXCDR_PH_RESET_ON_EIDLE                =0b0,
            p_RXCDR_LOCK_CFG                         =0b010101,

            # RX Initialization and Reset Attributes
            p_RXCDRFREQRESET_TIME                    =0b00001,
            p_RXCDRPHRESET_TIME                      =0b00001,
            p_RXISCANRESET_TIME                      =0b00001,
            p_RXPCSRESET_TIME                        =0b00001,
            p_RXPMARESET_TIME                        =0b00011,

            # RX OOB Signaling Attributes
            p_RXOOB_CFG                              =0b0000110,

            # RX Gearbox Attributes
            p_RXGEARBOX_EN                           ="FALSE",
            p_GEARBOX_MODE                           =0b000,

            # PRBS Detection Attribute
            p_RXPRBS_ERR_LOOPBACK                    =0b0,

            # Power-Down Attributes
            p_PD_TRANS_TIME_FROM_P2                  =0x03c,
            p_PD_TRANS_TIME_NONE_P2                  =0x3c,
            p_PD_TRANS_TIME_TO_P2                    =0x64,

            # RX OOB Signaling Attributes
            p_SAS_MAX_COM                            =64,
            p_SAS_MIN_COM                            =36,
            p_SATA_BURST_SEQ_LEN                     =0b0101,
            p_SATA_BURST_VAL                         =0b100,
            p_SATA_EIDLE_VAL                         =0b100,
            p_SATA_MAX_BURST                         =8,
            p_SATA_MAX_INIT                          =21,
            p_SATA_MAX_WAKE                          =7,
            p_SATA_MIN_BURST                         =4,
            p_SATA_MIN_INIT                          =12,
            p_SATA_MIN_WAKE                          =4,

            # RX Fabric Clock Output Control Attributes
            p_TRANS_TIME_RATE                        =0x0E,

            # TX Buffer Attributes
            p_TXBUF_EN                               ="FALSE",
            p_TXBUF_RESET_ON_RATE_CHANGE             ="TRUE",
            p_TXDLY_CFG                              =0x001F,
            p_TXDLY_LCFG                             =0x030,
            p_TXDLY_TAP_CFG                          =0x0000,
            p_TXPH_CFG                               =0x0780,
            p_TXPHDLY_CFG                            =0x084020,
            p_TXPH_MONITOR_SEL                       =0b00000,
            p_TX_XCLK_SEL                            ="TXUSR",

            # FPGA TX Interface Attributes
            p_TX_DATA_WIDTH                          =data_width,

            # TX Configurable Driver Attributes
            p_TX_DEEMPH0                             =0b00000,
            p_TX_DEEMPH1                             =0b00000,
            p_TX_EIDLE_ASSERT_DELAY                  =0b110,
            p_TX_EIDLE_DEASSERT_DELAY                =0b100,
            p_TX_LOOPBACK_DRIVE_HIZ                  ="FALSE",
            p_TX_MAINCURSOR_SEL                      =0b0,
            p_TX_DRIVE_MODE                          ="DIRECT",
            p_TX_MARGIN_FULL_0                       =0b1001110,
            p_TX_MARGIN_FULL_1                       =0b1001001,
            p_TX_MARGIN_FULL_2                       =0b1000101,
            p_TX_MARGIN_FULL_3                       =0b1000010,
            p_TX_MARGIN_FULL_4                       =0b1000000,
            p_TX_MARGIN_LOW_0                        =0b1000110,
            p_TX_MARGIN_LOW_1                        =0b1000100,
            p_TX_MARGIN_LOW_2                        =0b1000010,
            p_TX_MARGIN_LOW_3                        =0b1000000,
            p_TX_MARGIN_LOW_4                        =0b1000000,

            # TX Gearbox Attributes
            p_TXGEARBOX_EN                           ="FALSE",

            # TX Initialization and Reset Attributes
            p_TXPCSRESET_TIME                        =0b00001,
            p_TXPMARESET_TIME                        =0b00001,

            # TX Receiver Detection Attributes
            p_TX_RXDETECT_CFG                        =0x1832,
            p_TX_RXDETECT_REF                        =0b100,

            # CPLL Attributes
            p_CPLL_CFG                               =0xBC07DC,
            p_CPLL_FBDIV                             =1 if use_qpll else pll.config["n2"],
            p_CPLL_FBDIV_45                          =4 if use_qpll else pll.config["n1"],
            p_CPLL_INIT_CFG                          =0x00001E,
            p_CPLL_LOCK_CFG                          =0x01E8,
            p_CPLL_REFCLK_DIV                        =1 if use_qpll else pll.config["m"],
            p_RXOUT_DIV                              =pll.config["d"],
            p_TXOUT_DIV                              =pll.config["d"],
            p_SATA_CPLL_CFG                          ="VCO_3000MHZ",

            # RX Initialization and Reset Attributes
            p_RXDFELPMRESET_TIME                     =0b0001111,

            # RX Equalizer Attributes
            p_RXLPM_HF_CFG                           =0b00000011110000,
            p_RXLPM_LF_CFG                           =0b00000011110000,
            p_RX_DFE_GAIN_CFG                        =0x020FEA,
            p_RX_DFE_H2_CFG                          =0b000000000000,
            p_RX_DFE_H3_CFG                          =0b000001000000,
            p_RX_DFE_H4_CFG                          =0b00011110000,
            p_RX_DFE_H5_CFG                          =0b00011100000,
            p_RX_DFE_KL_CFG                          =0b0000011111110,
            p_RX_DFE_LPM_CFG                         =0x0954,
            p_RX_DFE_LPM_HOLD_DURING_EIDLE           =0b0,
            p_RX_DFE_UT_CFG                          =0b10001111000000000,
            p_RX_DFE_VP_CFG                          =0b00011111100000011,

            # Power-Down Attributes
            p_RX_CLKMUX_PD                           =0b1,
            p_TX_CLKMUX_PD                           =0b1,

            # FPGA RX Interface Attribute
            p_RX_INT_DATAWIDTH                       =data_width == 40,

            # FPGA TX Interface Attribute
            p_TX_INT_DATAWIDTH                       =data_width == 40,

            # TX Configurable Driver Attributes
            p_TX_QPI_STATUS_EN                       =0b0,

            # RX Equalizer Attributes
            p_RX_DFE_KL_CFG2                         =0x301148AC,
            p_RX_DFE_XYD_CFG                         =0b0000000000000,

            # TX Configurable Driver Attributes
            p_TX_PREDRIVER_MODE                      =0b0
        )
        gtx_params.update(
            # CPLL Ports
            #o_CPLLFBCLKLOST                  =,
            o_CPLLLOCK                       =Signal() if use_qpll else pll.lock,
            i_CPLLLOCKDETCLK                 =ClockSignal(),
            i_CPLLLOCKEN                     =1,
            i_CPLLPD                         =0,
            #o_CPLLREFCLKLOST                 =,
            i_CPLLREFCLKSEL                  =0b001,
            i_CPLLRESET                      =0 if use_qpll else pll.reset,
            i_GTRSVD                         =0b0000000000000000,
            i_PCSRSVDIN                      =0b0000000000000000,
            i_PCSRSVDIN2                     =0b00000,
            i_PMARSVDIN                      =0b00000,
            i_PMARSVDIN2                     =0b00000,
            i_TSTIN                          =0b11111111111111111111,
            #o_TSTOUT                         =,

            # Channel
            i_CLKRSVD                        =0b0000,

            # Channel - Clocking Ports
            i_GTGREFCLK                      =0,
            i_GTNORTHREFCLK0                 =0,
            i_GTNORTHREFCLK1                 =0,
            i_GTREFCLK0                      =0 if use_qpll else pll.refclk,
            i_GTREFCLK1                      =0,
            i_GTSOUTHREFCLK0                 =0,
            i_GTSOUTHREFCLK1                 =0,

            # Channel - DRP Ports
            i_DRPADDR                        =0,
            i_DRPCLK                         =0,
            i_DRPDI                          =0,
            #o_DRPDO                          =,
            i_DRPEN                          =0,
            #o_DRPRDY                         =,
            i_DRPWE                          =0,

            # Clocking Ports
            #o_GTREFCLKMONITOR                =,
            i_QPLLCLK                        =0 if use_cpll else pll.clk,
            i_QPLLREFCLK                     =0 if use_cpll else pll.refclk,
            i_RXSYSCLKSEL                    =0b11 if use_qpll else 0b00,
            i_TXSYSCLKSEL                    =0b11 if use_qpll else 0b00,

            # Digital Monitor Ports
            #o_DMONITOROUT                    =,

            # FPGA TX Interface Datapath Configuration
            i_TX8B10BEN                      =0,

            # Loopback Ports
            i_LOOPBACK                       =0b010 if internal_loopback else 0b000,

            # PCI Express Ports
            #o_PHYSTATUS                      =,
            i_RXRATE                         =0b000,
            #o_RXVALID                        =,

            # Power-Down Ports
            i_RXPD                           =0b00,
            i_TXPD                           =0b00,

            # RX 8B/10B Decoder Ports
            i_SETERRSTATUS                   =0,

            # RX Initialization and Reset Ports
            i_EYESCANRESET                   =0,
            i_RXUSERRDY                      =rx_init.Xxuserrdy,

            # RX Margin Analysis Ports
            #o_EYESCANDATAERROR               =,
            i_EYESCANMODE                    =0,
            i_EYESCANTRIGGER                 =0,

            # Receive Ports - CDR Ports
            i_RXCDRFREQRESET                 =0,
            i_RXCDRHOLD                      =0,
            #o_RXCDRLOCK                      =,
            i_RXCDROVRDEN                    =0,
            i_RXCDRRESET                     =0,
            i_RXCDRRESETRSV                  =0,

            # Receive Ports - Clock Correction Ports
            #o_RXCLKCORCNT                    =,

            # Receive Ports - FPGA RX Interface Datapath Configuration
            i_RX8B10BEN                      =0,

            # Receive Ports - FPGA RX Interface Ports
            i_RXUSRCLK                       =ClockSignal("rx"),
            i_RXUSRCLK2                      =ClockSignal("rx"),

            # Receive Ports - FPGA RX interface Ports
            o_RXDATA                         =Cat(*[rxdata[10*i:10*i+8] for i in range(nwords)]),

            # Receive Ports - Pattern Checker Ports
            #o_RXPRBSERR                      =,
            i_RXPRBSSEL                      =0b000,

            # Receive Ports - Pattern Checker ports
            i_RXPRBSCNTRESET                 =0,

            # Receive Ports - RX  Equalizer Ports
            i_RXDFEXYDEN                     =1,
            i_RXDFEXYDHOLD                   =0,
            i_RXDFEXYDOVRDEN                 =0,

            # Receive Ports - RX 8B/10B Decoder Ports
            i_RXDISPERR                      =Cat(*[rxdata[10*i+9] for i in range(nwords)]),
            #o_RXNOTINTABLE                   =,

            # Receive Ports - RX AFE
            i_GTXRXP                         =rx_pads.p,
            # Receive Ports - RX AFE Ports
            i_GTXRXN                         =rx_pads.n,

            # Receive Ports - RX Buffer Bypass Ports
            i_RXBUFRESET                     =0,
            #o_RXBUFSTATUS                    =,
            i_RXDDIEN                        =1,
            i_RXDLYBYPASS                    =0,
            i_RXDLYEN                        =0,
            i_RXDLYOVRDEN                    =0,
            i_RXDLYSRESET                    =rx_init.Xxdlysreset,
            o_RXDLYSRESETDONE                =rx_init.Xxdlysresetdone,
            i_RXPHALIGN                      =0,
            o_RXPHALIGNDONE                  =rx_init.Xxphaligndone,
            i_RXPHALIGNEN                    =0,
            i_RXPHDLYPD                      =0,
            i_RXPHDLYRESET                   =0,
            #o_RXPHMONITOR                    =,
            i_RXPHOVRDEN                     =0,
            #o_RXPHSLIPMONITOR                =,
            #o_RXSTATUS                       =,

            # Receive Ports - RX Byte and Word Alignment Ports
            #o_RXBYTEISALIGNED                =,
            #o_RXBYTEREALIGN                  =,
            #o_RXCOMMADET                     =,
            i_RXCOMMADETEN                   =1,
            i_RXMCOMMAALIGNEN                =0,
            i_RXPCOMMAALIGNEN                =0,

            # Receive Ports - RX Channel Bonding Ports
            #o_RXCHANBONDSEQ                  =,
            i_RXCHBONDEN                     =0,
            i_RXCHBONDLEVEL                  =0b000,
            i_RXCHBONDMASTER                 =0,
            #o_RXCHBONDO                      =,
            i_RXCHBONDSLAVE                  =0,

            # Receive Ports - RX Channel Bonding Ports
            #o_RXCHANISALIGNED                =,
            #o_RXCHANREALIGN                  =,

            # Receive Ports - RX Equailizer Ports
            i_RXLPMHFHOLD                    =0,
            i_RXLPMHFOVRDEN                  =0,
            i_RXLPMLFHOLD                    =0,

            # Receive Ports - RX Equalizer Ports
            i_RXDFEAGCHOLD                   =0,
            i_RXDFEAGCOVRDEN                 =0,
            i_RXDFECM1EN                     =0,
            i_RXDFELFHOLD                    =0,
            i_RXDFELFOVRDEN                  =1,
            i_RXDFELPMRESET                  =0,
            i_RXDFETAP2HOLD                  =0,
            i_RXDFETAP2OVRDEN                =0,
            i_RXDFETAP3HOLD                  =0,
            i_RXDFETAP3OVRDEN                =0,
            i_RXDFETAP4HOLD                  =0,
            i_RXDFETAP4OVRDEN                =0,
            i_RXDFETAP5HOLD                  =0,
            i_RXDFETAP5OVRDEN                =0,
            i_RXDFEUTHOLD                    =0,
            i_RXDFEUTOVRDEN                  =0,
            i_RXDFEVPHOLD                    =0,
            i_RXDFEVPOVRDEN                  =0,
            i_RXDFEVSEN                      =0,
            i_RXLPMLFKLOVRDEN                =0,
            #o_RXMONITOROUT                   =
            i_RXMONITORSEL                   =0,
            i_RXOSHOLD                       =0,
            i_RXOSOVRDEN                     =0,

            # Receive Ports - RX Fabric ClocK Output Control Ports
            #o_RXRATEDONE                     =,

            # Receive Ports - RX Fabric Output Control Ports
            o_RXOUTCLK                       =self.rxoutclk,
            #o_RXOUTCLKFABRIC                 =,
            #o_RXOUTCLKPCS                    =,
            i_RXOUTCLKSEL                    =0b010,

            # Receive Ports - RX Gearbox Ports
            #o_RXDATAVALID                    =,
            #o_RXHEADER                       =,
            #o_RXHEADERVALID                  =,
            #o_RXSTARTOFSEQ                   =,

            # Receive Ports - RX Gearbox Ports
            i_RXGEARBOXSLIP                  =0,

            # Receive Ports - RX Initialization and Reset Ports
            i_GTRXRESET                      =rx_init.gtXxreset,
            i_RXOOBRESET                     =0,
            i_RXPCSRESET                     =0,
            i_RXPMARESET                     =0,

            # Receive Ports - RX Margin Analysis ports
            i_RXLPMEN                        =0,

            # Receive Ports - RX OOB Signaling ports
            #o_RXCOMSASDET                    =,
            #o_RXCOMWAKEDET                   =,

            # Receive Ports - RX OOB Signaling ports
            #o_RXCOMINITDET                   =,

            # Receive Ports - RX OOB signalling Ports
            #o_RXELECIDLE                     =,
            i_RXELECIDLEMODE                 =0b11,

            # Receive Ports - RX Polarity Control Ports
            i_RXPOLARITY                     =rx_polarity,

            # Receive Ports - RX gearbox ports
            i_RXSLIDE                        =0,

            # Receive Ports - RX8B/10B Decoder Ports
            #o_RXCHARISCOMMA                  =,
            o_RXCHARISK                      =Cat(*[rxdata[10*i+8] for i in range(nwords)]),

            # Receive Ports - Rx Channel Bonding Ports
            i_RXCHBONDI                      =0b00000,

            # Receive Ports -RX Initialization and Reset Ports
            o_RXRESETDONE                    =rx_init.Xxresetdone,

            # Rx AFE Ports
            i_RXQPIEN                        =0,
            #o_RXQPISENN                      =,
            #o_RXQPISENP                      =,

            # TX Buffer Bypass Ports
            i_TXPHDLYTSTCLK                  =0,

            # TX Configurable Driver Ports
            i_TXPOSTCURSOR                   =0b00000,
            i_TXPOSTCURSORINV                =0,
            i_TXPRECURSOR                    =0b00000,
            i_TXPRECURSORINV                 =0,
            i_TXQPIBIASEN                    =0,
            i_TXQPISTRONGPDOWN               =0,
            i_TXQPIWEAKPUP                   =0,

            # TX Initialization and Reset Ports
            i_CFGRESET                       =0,
            i_GTTXRESET                      =tx_init.gtXxreset,
            #o_PCSRSVDOUT                     =,
            i_TXUSERRDY                      =tx_init.Xxuserrdy,

            # Transceiver Reset Mode Operation
            i_GTRESETSEL                     =0,
            i_RESETOVRD                      =0,

            # Transmit Ports - 8b10b Encoder Control Ports
            i_TXCHARDISPMODE                 =Cat(*[txdata[10*i+9] for i in range(nwords)]),
            i_TXCHARDISPVAL                  =Cat(*[txdata[10*i+8] for i in range(nwords)]),

            # Transmit Ports - FPGA TX Interface Ports
            i_TXUSRCLK                       =ClockSignal("tx"),
            i_TXUSRCLK2                      =ClockSignal("tx"),

            # Transmit Ports - PCI Express Ports
            i_TXELECIDLE                     =0,
            i_TXMARGIN                       =0b000,
            i_TXRATE                         =0b000,
            i_TXSWING                        =0,

            # Transmit Ports - Pattern Generator Ports
            i_TXPRBSFORCEERR                 =0,

            # Transmit Ports - TX Buffer Bypass Ports
            i_TXDLYBYPASS                    =0,
            i_TXDLYEN                        =0,
            i_TXDLYHOLD                      =0,
            i_TXDLYOVRDEN                    =0,
            i_TXDLYSRESET                    =tx_init.Xxdlysreset,
            o_TXDLYSRESETDONE                =tx_init.Xxdlysresetdone,
            i_TXDLYUPDOWN                    =0,
            i_TXPHALIGN                      =0,
            o_TXPHALIGNDONE                  =tx_init.Xxphaligndone,
            i_TXPHALIGNEN                    =0,
            i_TXPHDLYPD                      =0,
            i_TXPHDLYRESET                   =0,
            i_TXPHINIT                       =0,
            #o_TXPHINITDONE                   =,
            i_TXPHOVRDEN                     =0,

            # Transmit Ports - TX Buffer Ports
            #o_TXBUFSTATUS                    =,

            # Transmit Ports - TX Configurable Driver Ports
            i_TXBUFDIFFCTRL                  =0b100,
            i_TXDEEMPH                       =0,
            i_TXDIFFCTRL                     =0b1000,
            i_TXDIFFPD                       =0,
            i_TXINHIBIT                      =0,
            i_TXMAINCURSOR                   =0b0000000,
            i_TXPISOPD                       =0,

            # Transmit Ports - TX Data Path interface
            i_TXDATA                         =Cat(*[txdata[10*i:10*i+8] for i in range(nwords)]),

            # Transmit Ports - TX Driver and OOB signaling
            o_GTXTXN                         =tx_pads.n,
            o_GTXTXP                         =tx_pads.p,

            # Transmit Ports - TX Fabric Clock Output Control Ports
            o_TXOUTCLK                       =self.txoutclk,
            #o_TXOUTCLKFABRIC                 =,
            #o_TXOUTCLKPCS                    =,
            i_TXOUTCLKSEL                    =0b011,
            #o_TXRATEDONE                     =,

            # Transmit Ports - TX Gearbox Ports
            i_TXCHARISK                      =0b00000000,
            #o_TXGEARBOXREADY                 =,
            i_TXHEADER                       =0b000,
            i_TXSEQUENCE                     =0b0000000,
            i_TXSTARTSEQ                     =0,

            # Transmit Ports - TX Initialization and Reset Ports
            i_TXPCSRESET                     =0,
            i_TXPMARESET                     =0,
            o_TXRESETDONE                    =tx_init.Xxresetdone,

            # Transmit Ports - TX OOB signaling Ports
            #o_TXCOMFINISH                    =,
            i_TXCOMINIT                      =0,
            i_TXCOMSAS                       =0,
            i_TXCOMWAKE                      =0,
            i_TXPDELECIDLEMODE               =0,

            # Transmit Ports - TX Polarity Control Ports
            i_TXPOLARITY                     =tx_polarity,

            # Transmit Ports - TX Receiver Detection Ports
            i_TXDETECTRX                     =0,

            # Transmit Ports - TX8b/10b Encoder Ports
            i_TX8B10BBYPASS                  =0b00000000,

            # Transmit Ports - pattern Generator Ports
            i_TXPRBSSEL                      =0b000,

            # Tx Configurable Driver  Ports
            #o_TXQPISENN                      =,
            #o_TXQPISENP                      =,
            )
        self.specials += Instance("GTXE2_CHANNEL", **gtx_params)

        # tx clocking
        tx_reset_deglitched = Signal()
        tx_reset_deglitched.attr.add("no_retiming")
        self.sync += tx_reset_deglitched.eq(~tx_init.done)
        self.clock_domains.cd_tx = ClockDomain()
        txoutclk_bufg = Signal()
        txoutclk_bufr = Signal()
        tx_bufr_div = pll.config["clkin"]/self.tx_clk_freq
        assert tx_bufr_div == int(tx_bufr_div)
        self.specials += [
            Instance("BUFG", i_I=self.txoutclk, o_O=txoutclk_bufg),
            # TODO: use MMCM instead?
            Instance("BUFR", i_I=txoutclk_bufg, o_O=txoutclk_bufr,
                i_CE=1, p_BUFR_DIVIDE=str(int(tx_bufr_div))),
            Instance("BUFG", i_I=txoutclk_bufr, o_O=self.cd_tx.clk),
            AsyncResetSynchronizer(self.cd_tx, tx_reset_deglitched)
        ]

        # rx clocking
        rx_reset_deglitched = Signal()
        rx_reset_deglitched.attr.add("no_retiming")
        self.sync.tx += rx_reset_deglitched.eq(~rx_init.done)
        self.clock_domains.cd_rx = ClockDomain()
        self.specials += [
            Instance("BUFG", i_I=self.rxoutclk, o_O=self.cd_rx.clk),
            AsyncResetSynchronizer(self.cd_rx, rx_reset_deglitched)
        ]

        # tx data and prbs
        self.submodules.tx_prbs = ClockDomainsRenamer("tx")(PRBSTX(20, True))
        self.comb += self.tx_prbs.config.eq(tx_prbs_config)
        self.comb += [
            self.tx_prbs.i.eq(Cat(*[self.encoder.output[i] for i in range(2)])),
            If(tx_produce_square_wave,
                # square wave @ linerate/20 for scope observation
                txdata.eq(0b11111111110000000000)
            ).Else(
                txdata.eq(self.tx_prbs.o)
            )
        ]

        # rx data and prbs
        self.submodules.rx_prbs = ClockDomainsRenamer("rx")(PRBSRX(20, True))
        self.comb += [
            self.rx_prbs.config.eq(rx_prbs_config),
            rx_prbs_errors.eq(self.rx_prbs.errors)
        ]
        self.comb += [
            self.decoders[0].input.eq(rxdata[:10]),
            self.decoders[1].input.eq(rxdata[10:]),
            self.rx_prbs.i.eq(rxdata)
        ]

        # clock alignment
        if clock_aligner:
            clock_aligner = BruteforceClockAligner(0b0101111100, self.tx_clk_freq)
            self.submodules += clock_aligner
            self.comb += [
                clock_aligner.rxdata.eq(rxdata),
                rx_init.restart.eq(clock_aligner.restart),
                self.rx_ready.eq(clock_aligner.ready)
            ]
        else:
            self.comb += self.rx_ready.eq(rx_init.done)