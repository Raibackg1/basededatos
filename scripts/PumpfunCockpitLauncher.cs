using System;
using System.Collections;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.Globalization;
using System.IO;
using System.Net;
using System.Reflection;
using System.Runtime.InteropServices;
using System.Text;
using System.Threading;
using System.Web.Script.Serialization;
using System.Windows.Forms;

namespace PumpfunCockpit
{
    public class MainForm : Form
    {
        [DllImport("dwmapi.dll")]
        private static extern int DwmSetWindowAttribute(IntPtr hwnd, int attr, ref int attrValue, int attrSize);

        private Process _serverProcess;
        private System.Windows.Forms.Timer _pollTimer;
        private Label _lblStatus;
        private Label _lblBalanceVal;
        private Label _lblBalanceSub;
        private Label _lblPositionsVal;
        private Label _lblPositionsSub;
        private Label _lblShadowVal;
        private Label _lblShadowSub;
        private Label _lblEvolutionVal;
        private Label _lblEvolutionSub;

        private TabControl _tabControl;
        private DataGridView _gridRadar;
        private DataGridView _gridPositions;
        private DataGridView _gridShadow;
        private DataGridView _gridPipeline;
        private RichTextBox _txtPipelineStages;
        private Label _lblFunnelInfo;
        private RichTextBox _txtLogs;
        private RichTextBox _txtAiResponse;
        private TextBox _txtPrompt;

        private Button _btnToggleServer;
        private Button _btnToggleBot;
        private Button _btnPanicClose;
        private Button _btnOpenWeb;
        private Button _btnRefreshRadar;
        private Button _btnTriggerEvolve;
        private Button _btnSendAi;

        private ToolStripStatusLabel _statusFeed;
        private ToolStripStatusLabel _statusPriors;
        private ToolStripStatusLabel _statusToast;
        private ToolStripStatusLabel _statusRefresh;

        private NotifyIcon _trayIcon;
        private string _baseUrl = "http://127.0.0.1:8000";
        private int _lastTrackedShadow = -1;
        private int _lastLogId = 0;
        private bool _autoBotEnabled = false;
        private ArrayList _cachedPipelineTraces = null;
        private JavaScriptSerializer _serializer = new JavaScriptSerializer();

        // Ley anti-falacia (Plan Maestro V2 §6.1): sin medición real NO se pinta un
        // literal default ("$10,000.00", "0.91", "85.0"); se declara ausente.
        private const string ND = "N/D — sin dato";

        [STAThread]
        public static void Main()
        {
            try
            {
                Application.EnableVisualStyles();
                Application.SetCompatibleTextRenderingDefault(false);
                Application.Run(new MainForm());
            }
            catch (Exception ex)
            {
                File.WriteAllText("crash.log", ex.ToString());
                MessageBox.Show(ex.ToString(), "Crash");
            }
        }

        public MainForm()
        {
            try
            {
                ServicePointManager.DefaultConnectionLimit = 32;
            }
            catch { }

            InitializeComponent();
            CheckBackendHealth();
            _pollTimer = new System.Windows.Forms.Timer();
            _pollTimer.Interval = 3500;
            _pollTimer.Tick += (s, e) => PollAllData();
            _pollTimer.Start();
        }

        protected override void OnHandleCreated(EventArgs e)
        {
            base.OnHandleCreated(e);
            try
            {
                int darkMode = 1;
                // DWMWA_USE_IMMERSIVE_DARK_MODE (20 on Windows 10 build 18985+ and Windows 11)
                DwmSetWindowAttribute(this.Handle, 20, ref darkMode, sizeof(int));
            }
            catch { }
        }

        private void InitializeComponent()
        {
            this.Text = "PumpScope Cockpit — Native High-Frequency Quant Terminal (Solana DEX Real)";
            this.Size = new Size(1300, 860);
            this.MinimumSize = new Size(1050, 720);
            this.BackColor = Color.FromArgb(10, 13, 20);
            this.ForeColor = Color.FromArgb(226, 232, 240);
            this.Font = new Font("Segoe UI", 9.25f, FontStyle.Regular);
            this.StartPosition = FormStartPosition.CenterScreen;

            // Form Closing Handler: Clean shutdown of background processes and tray icon
            this.FormClosing += (s, e) => CleanShutdown();

            // System Tray Icon
            _trayIcon = new NotifyIcon();
            _trayIcon.Text = "PumpScope Cockpit Monitor";
            _trayIcon.Icon = SystemIcons.Shield;
            _trayIcon.Visible = true;
            _trayIcon.DoubleClick += (s, e) =>
            {
                this.Show();
                this.WindowState = FormWindowState.Normal;
                this.BringToFront();
            };

            // Status Bar at Bottom
            StatusStrip statusStrip = new StatusStrip();
            statusStrip.BackColor = Color.FromArgb(16, 21, 30);
            statusStrip.ForeColor = Color.FromArgb(148, 163, 184);
            statusStrip.Font = new Font("Segoe UI", 8.5f);

            _statusFeed = new ToolStripStatusLabel();
            _statusFeed.Text = "🌐 DEX Feed: Conectando...";
            _statusFeed.ForeColor = Color.FromArgb(0, 229, 255);
            _statusFeed.Margin = new Padding(10, 3, 15, 3);
            statusStrip.Items.Add(_statusFeed);

            _statusPriors = new ToolStripStatusLabel();
            _statusPriors.Text = "📊 Ground Truth: 5,021 cierres · WR 12.55% · PF 0.269 · SL Real -33.49% · PAPER ONLY";
            _statusPriors.ForeColor = Color.FromArgb(203, 213, 225);
            _statusPriors.Spring = true;
            _statusPriors.TextAlign = ContentAlignment.MiddleLeft;
            statusStrip.Items.Add(_statusPriors);

            _statusToast = new ToolStripStatusLabel();
            _statusToast.Text = "";
            _statusToast.ForeColor = Color.FromArgb(0, 255, 170);
            _statusToast.Margin = new Padding(10, 3, 10, 3);
            statusStrip.Items.Add(_statusToast);

            _statusRefresh = new ToolStripStatusLabel();
            _statusRefresh.Text = "⏱ Refresco: 3.5s";
            _statusRefresh.Margin = new Padding(10, 3, 10, 3);
            statusStrip.Items.Add(_statusRefresh);

            this.Controls.Add(statusStrip);

            // Container Panel for Top Stack (ensures strict top-to-bottom layout, zero docking conflicts)
            Panel topContainer = new Panel();
            topContainer.Dock = DockStyle.Top;
            topContainer.Height = 175;
            topContainer.BackColor = Color.FromArgb(10, 13, 20);

            // 1. Header Bar
            Panel headerPanel = new Panel();
            headerPanel.Location = new Point(0, 0);
            headerPanel.Size = new Size(topContainer.Width, 65);
            headerPanel.Anchor = AnchorStyles.Top | AnchorStyles.Left | AnchorStyles.Right;
            headerPanel.BackColor = Color.FromArgb(18, 24, 37);
            headerPanel.BorderStyle = BorderStyle.FixedSingle;
            headerPanel.Padding = new Padding(16, 10, 16, 10);

            Label title = new Label();
            title.Text = "⚡ PUMPSCOPE QUANT COCKPIT — HIGH FREQUENCY AUTONOMOUS DESKTOP";
            title.Font = new Font("Segoe UI", 11.5f, FontStyle.Bold);
            title.ForeColor = Color.FromArgb(88, 166, 255);
            title.AutoSize = true;
            title.Location = new Point(14, 10);
            headerPanel.Controls.Add(title);

            _lblStatus = new Label();
            _lblStatus.Text = "● Conectando con servidor local 127.0.0.1:8000...";
            _lblStatus.Font = new Font("Segoe UI", 9f, FontStyle.Bold);
            _lblStatus.ForeColor = Color.FromArgb(245, 158, 11);
            _lblStatus.AutoSize = true;
            _lblStatus.Location = new Point(16, 36);
            headerPanel.Controls.Add(_lblStatus);

            // Header Action Buttons
            int btnY = 14;
            int btnHeight = 36;

            _btnRefreshRadar = new Button();
            _btnRefreshRadar.Text = "🔄 Refrescar";
            _btnRefreshRadar.Font = new Font("Segoe UI", 8.75f, FontStyle.Bold);
            _btnRefreshRadar.Size = new Size(100, btnHeight);
            _btnRefreshRadar.Location = new Point(headerPanel.Width - 670, btnY);
            _btnRefreshRadar.Anchor = AnchorStyles.Top | AnchorStyles.Right;
            _btnRefreshRadar.FlatStyle = FlatStyle.Flat;
            _btnRefreshRadar.BackColor = Color.FromArgb(33, 44, 61);
            _btnRefreshRadar.ForeColor = Color.White;
            _btnRefreshRadar.Cursor = Cursors.Hand;
            _btnRefreshRadar.FlatAppearance.BorderSize = 0;
            _btnRefreshRadar.Click += (s, e) => PollAllData();
            headerPanel.Controls.Add(_btnRefreshRadar);

            _btnToggleBot = new Button();
            _btnToggleBot.Text = "🔴 Bot: PAUSADO";
            _btnToggleBot.Font = new Font("Segoe UI", 8.75f, FontStyle.Bold);
            _btnToggleBot.Size = new Size(130, btnHeight);
            _btnToggleBot.Location = new Point(headerPanel.Width - 560, btnY);
            _btnToggleBot.Anchor = AnchorStyles.Top | AnchorStyles.Right;
            _btnToggleBot.FlatStyle = FlatStyle.Flat;
            _btnToggleBot.BackColor = Color.FromArgb(160, 30, 30);
            _btnToggleBot.ForeColor = Color.White;
            _btnToggleBot.Cursor = Cursors.Hand;
            _btnToggleBot.FlatAppearance.BorderSize = 0;
            _btnToggleBot.Click += (s, e) => ToggleBotSniper();
            headerPanel.Controls.Add(_btnToggleBot);

            _btnPanicClose = new Button();
            _btnPanicClose.Text = "🚨 Salida Pánico";
            _btnPanicClose.Font = new Font("Segoe UI", 8.75f, FontStyle.Bold);
            _btnPanicClose.Size = new Size(125, btnHeight);
            _btnPanicClose.Location = new Point(headerPanel.Width - 420, btnY);
            _btnPanicClose.Anchor = AnchorStyles.Top | AnchorStyles.Right;
            _btnPanicClose.FlatStyle = FlatStyle.Flat;
            _btnPanicClose.BackColor = Color.FromArgb(180, 40, 40);
            _btnPanicClose.ForeColor = Color.White;
            _btnPanicClose.Cursor = Cursors.Hand;
            _btnPanicClose.FlatAppearance.BorderSize = 0;
            _btnPanicClose.Click += (s, e) => PanicCloseAllPositions();
            headerPanel.Controls.Add(_btnPanicClose);

            _btnOpenWeb = new Button();
            _btnOpenWeb.Text = "🌐 Abrir Cockpit App";
            _btnOpenWeb.Font = new Font("Segoe UI", 8.75f, FontStyle.Bold);
            _btnOpenWeb.Size = new Size(140, btnHeight);
            _btnOpenWeb.Location = new Point(headerPanel.Width - 285, btnY);
            _btnOpenWeb.Anchor = AnchorStyles.Top | AnchorStyles.Right;
            _btnOpenWeb.FlatStyle = FlatStyle.Flat;
            _btnOpenWeb.BackColor = Color.FromArgb(31, 111, 235);
            _btnOpenWeb.ForeColor = Color.White;
            _btnOpenWeb.Cursor = Cursors.Hand;
            _btnOpenWeb.FlatAppearance.BorderSize = 0;
            _btnOpenWeb.Click += (s, e) => LaunchStandaloneCockpit();
            headerPanel.Controls.Add(_btnOpenWeb);

            _btnToggleServer = new Button();
            _btnToggleServer.Text = "■ Detener Server";
            _btnToggleServer.Font = new Font("Segoe UI", 8.75f, FontStyle.Bold);
            _btnToggleServer.Size = new Size(130, btnHeight);
            _btnToggleServer.Location = new Point(headerPanel.Width - 135, btnY);
            _btnToggleServer.Anchor = AnchorStyles.Top | AnchorStyles.Right;
            _btnToggleServer.FlatStyle = FlatStyle.Flat;
            _btnToggleServer.BackColor = Color.FromArgb(218, 54, 51);
            _btnToggleServer.ForeColor = Color.White;
            _btnToggleServer.Cursor = Cursors.Hand;
            _btnToggleServer.FlatAppearance.BorderSize = 0;
            _btnToggleServer.Click += (s, e) => ToggleServer();
            headerPanel.Controls.Add(_btnToggleServer);

            topContainer.Controls.Add(headerPanel);

            // 2. KPI Cards Panel (Located directly under header)
            Panel kpiPanel = new Panel();
            kpiPanel.Location = new Point(0, 68);
            kpiPanel.Size = new Size(topContainer.Width, 102);
            kpiPanel.Anchor = AnchorStyles.Top | AnchorStyles.Left | AnchorStyles.Right;
            kpiPanel.BackColor = Color.FromArgb(10, 13, 20);
            kpiPanel.Padding = new Padding(12, 6, 12, 6);

            int cardWidth = 290;
            int cardGap = 14;

            // Card 1: Balance (valores canónicos del bloque `dashboard.paper_wallet`)
            Panel card1 = CreateKpiCard("SALDO PAPER (REAL DEX)", ND, "Esperando datos del servidor…",
                                        Color.FromArgb(0, 255, 170), out _lblBalanceVal, out _lblBalanceSub);
            card1.Location = new Point(12, 6);
            card1.Size = new Size(cardWidth, 88);
            kpiPanel.Controls.Add(card1);

            // Card 2: Positions
            Panel card2 = CreateKpiCard("POSICIONES EN CURSO", ND, "TP1 +50% | BE +25% | SL -33.49%",
                                        Color.FromArgb(0, 229, 255), out _lblPositionsVal, out _lblPositionsSub);
            card2.Location = new Point(12 + (cardWidth + cardGap), 6);
            card2.Size = new Size(cardWidth, 88);
            kpiPanel.Controls.Add(card2);

            // Card 3: Shadow Veto
            Panel card3 = CreateKpiCard("SHADOW VETO SAVES", ND, "True Saves / Falsos Vetos: N/D",
                                        Color.FromArgb(168, 85, 247), out _lblShadowVal, out _lblShadowSub);
            card3.Location = new Point(12 + (cardWidth + cardGap) * 2, 6);
            card3.Size = new Size(cardWidth, 88);
            kpiPanel.Controls.Add(card3);

            // Card 4: Evolution
            Panel card4 = CreateKpiCard("AUTO-APRENDIZAJE & ML", ND, "Reglas adaptativas: N/D",
                                        Color.FromArgb(245, 158, 11), out _lblEvolutionVal, out _lblEvolutionSub);
            card4.Location = new Point(12 + (cardWidth + cardGap) * 3, 6);
            card4.Size = new Size(cardWidth, 88);
            kpiPanel.Controls.Add(card4);

            topContainer.Controls.Add(kpiPanel);
            this.Controls.Add(topContainer);

            // 3. TabControl in Center (Fill)
            _tabControl = new TabControl();
            _tabControl.Dock = DockStyle.Fill;
            _tabControl.Font = new Font("Segoe UI", 9.5f, FontStyle.Bold);
            _tabControl.Padding = new Point(16, 6);

            // Tab 1: Live Radar
            TabPage tabRadar = new TabPage("📡 Radar DEX en Vivo");
            tabRadar.BackColor = Color.FromArgb(10, 13, 20);
            InitializeRadarTab(tabRadar);
            _tabControl.TabPages.Add(tabRadar);

            // Tab 2: Decision Pipeline & Funnel (P1)
            TabPage tabPipeline = new TabPage("🔬 Pipeline & Embudo");
            tabPipeline.BackColor = Color.FromArgb(10, 13, 20);
            InitializePipelineTab(tabPipeline);
            _tabControl.TabPages.Add(tabPipeline);

            // Tab 3: Paper Positions
            TabPage tabPositions = new TabPage("💼 Posiciones Paper");
            tabPositions.BackColor = Color.FromArgb(10, 13, 20);
            InitializePositionsTab(tabPositions);
            _tabControl.TabPages.Add(tabPositions);

            // Tab 4: Shadow Veto Ledger
            TabPage tabShadow = new TabPage("🛡️ Shadow Veto Ledger");
            tabShadow.BackColor = Color.FromArgb(10, 13, 20);
            InitializeShadowTab(tabShadow);
            _tabControl.TabPages.Add(tabShadow);

            // Tab 5: Live Terminal & Logs
            TabPage tabLogs = new TabPage("📜 Terminal & Logs en Vivo");
            tabLogs.BackColor = Color.FromArgb(10, 13, 20);
            InitializeLogsTab(tabLogs);
            _tabControl.TabPages.Add(tabLogs);

            // Tab 6: AI Terminal
            TabPage tabAi = new TabPage("🤖 Terminal IA & Comandos");
            tabAi.BackColor = Color.FromArgb(10, 13, 20);
            InitializeAiTab(tabAi);
            _tabControl.TabPages.Add(tabAi);

            this.Controls.Add(_tabControl);
            _tabControl.BringToFront();
        }

        private static void EnableDoubleBuffering(Control c)
        {
            try
            {
                typeof(Control).InvokeMember("DoubleBuffered",
                    BindingFlags.NonPublic | BindingFlags.Instance | BindingFlags.SetProperty,
                    null, c, new object[] { true });
            }
            catch { }
        }

        private static int GetSelectedRowIndex(DataGridView grid)
        {
            if (grid == null || grid.SelectedRows.Count == 0) return -1;
            return grid.SelectedRows[0].Index;
        }

        private static void RestoreSelection(DataGridView grid, int savedIndex, int savedScroll)
        {
            if (grid == null || grid.Rows.Count == 0) return;
            try
            {
                if (savedScroll >= 0 && savedScroll < grid.Rows.Count)
                    grid.FirstDisplayedScrollingRowIndex = savedScroll;
                if (savedIndex >= 0 && savedIndex < grid.Rows.Count)
                {
                    grid.ClearSelection();
                    grid.Rows[savedIndex].Selected = true;
                }
            }
            catch { }
        }

        private void ShowToast(string message)
        {
            if (this.InvokeRequired)
            {
                this.BeginInvoke(new Action<string>(ShowToast), message);
                return;
            }
            if (_statusToast != null)
            {
                _statusToast.Text = message;
                _statusToast.ForeColor = Color.FromArgb(0, 255, 170);
            }
        }

        private Panel CreateKpiCard(string header, string defaultVal, string defaultSub, Color accent,
                                    out Label valLabel, out Label subLabel)
        {
            Panel card = new Panel();
            card.BackColor = Color.FromArgb(18, 24, 37);
            card.BorderStyle = BorderStyle.FixedSingle;

            Label title = new Label();
            title.Text = header;
            title.Font = new Font("Segoe UI", 7.75f, FontStyle.Bold);
            title.ForeColor = Color.FromArgb(148, 163, 184);
            title.Location = new Point(12, 8);
            title.Size = new Size(260, 16);
            card.Controls.Add(title);

            valLabel = new Label();
            valLabel.Text = defaultVal;
            valLabel.Font = new Font("Segoe UI", 13.5f, FontStyle.Bold);
            valLabel.ForeColor = accent;
            valLabel.Location = new Point(11, 26);
            valLabel.Size = new Size(265, 28);
            card.Controls.Add(valLabel);

            subLabel = new Label();
            subLabel.Text = defaultSub;
            subLabel.Font = new Font("Segoe UI", 8.25f, FontStyle.Regular);
            subLabel.ForeColor = Color.FromArgb(203, 213, 225);
            subLabel.Location = new Point(12, 58);
            subLabel.Size = new Size(265, 20);
            card.Controls.Add(subLabel);

            return card;
        }

        private void InitializeRadarTab(TabPage tab)
        {
            Panel bottomBar = new Panel();
            bottomBar.Dock = DockStyle.Bottom;
            bottomBar.Height = 42;
            bottomBar.BackColor = Color.FromArgb(16, 21, 30);
            bottomBar.Padding = new Padding(12, 8, 12, 8);

            Label hint = new Label();
            hint.Text = "💡 Clic derecho en cualquier token para ver Dossier Forense, abrir DexScreener o copiar Mint.";
            hint.Font = new Font("Segoe UI", 8.75f, FontStyle.Italic);
            hint.ForeColor = Color.FromArgb(148, 163, 184);
            hint.AutoSize = true;
            hint.Location = new Point(12, 11);
            bottomBar.Controls.Add(hint);

            Button btnInspect = new Button();
            btnInspect.Text = "🔍 Inspeccionar Dossier";
            btnInspect.Size = new Size(160, 26);
            btnInspect.Location = new Point(bottomBar.Width - 340, 7);
            btnInspect.Anchor = AnchorStyles.Top | AnchorStyles.Right;
            btnInspect.FlatStyle = FlatStyle.Flat;
            btnInspect.BackColor = Color.FromArgb(137, 87, 229);
            btnInspect.ForeColor = Color.White;
            btnInspect.Cursor = Cursors.Hand;
            btnInspect.Click += (s, e) => InspectSelectedRadarToken();
            bottomBar.Controls.Add(btnInspect);

            Button btnOpenDex = new Button();
            btnOpenDex.Text = "Abrir en DexScreener";
            btnOpenDex.Size = new Size(150, 26);
            btnOpenDex.Location = new Point(bottomBar.Width - 170, 7);
            btnOpenDex.Anchor = AnchorStyles.Top | AnchorStyles.Right;
            btnOpenDex.FlatStyle = FlatStyle.Flat;
            btnOpenDex.BackColor = Color.FromArgb(31, 111, 235);
            btnOpenDex.ForeColor = Color.White;
            btnOpenDex.Cursor = Cursors.Hand;
            btnOpenDex.Click += (s, e) => OpenSelectedTokenInDex();
            bottomBar.Controls.Add(btnOpenDex);

            tab.Controls.Add(bottomBar);

            _gridRadar = CreateStyledGrid();
            _gridRadar.Dock = DockStyle.Fill;
            _gridRadar.Columns.Add("hora", "Hora");
            _gridRadar.Columns.Add("symbol", "Símbolo");
            _gridRadar.Columns.Add("mint", "Mint (Dirección On-Chain)");
            _gridRadar.Columns.Add("mc", "Market Cap ($)");
            _gridRadar.Columns.Add("liq", "Liquidez ($)");
            _gridRadar.Columns.Add("verdict", "Veredicto");
            _gridRadar.Columns.Add("reason", "Diagnóstico / Motivo Veto");

            _gridRadar.Columns[0].Width = 70;
            _gridRadar.Columns[1].Width = 90;
            _gridRadar.Columns[2].Width = 240;
            _gridRadar.Columns[3].Width = 110;
            _gridRadar.Columns[4].Width = 100;
            _gridRadar.Columns[5].Width = 95;
            _gridRadar.Columns[6].AutoSizeMode = DataGridViewAutoSizeColumnMode.Fill;

            _gridRadar.CellDoubleClick += (s, e) =>
            {
                if (e.RowIndex >= 0 && e.RowIndex < _gridRadar.Rows.Count)
                {
                    object cellVal = _gridRadar.Rows[e.RowIndex].Cells[2].Value;
                    if (cellVal != null)
                    {
                        string mint = cellVal.ToString();
                        Clipboard.SetText(mint);
                        ShowToast("📋 Mint copiado al portapapeles: " + mint);
                    }
                }
            };

            ContextMenuStrip ctxRadar = new ContextMenuStrip();
            ctxRadar.Items.Add("📋 Copiar Mint", null, (s, e) =>
            {
                if (_gridRadar.SelectedRows.Count > 0)
                {
                    string m = _gridRadar.SelectedRows[0].Cells[2].Value.ToString();
                    Clipboard.SetText(m);
                    ShowToast("📋 Mint copiado: " + m);
                }
            });
            ctxRadar.Items.Add("🔍 Inspeccionar Dossier Forense", null, (s, e) => InspectSelectedRadarToken());
            ctxRadar.Items.Add("📈 Abrir en DexScreener", null, (s, e) => OpenSelectedTokenInDex());
            ctxRadar.Items.Add("🛡️ Abrir en RugCheck", null, (s, e) =>
            {
                if (_gridRadar.SelectedRows.Count > 0)
                {
                    string m = _gridRadar.SelectedRows[0].Cells[2].Value.ToString();
                    Process.Start("https://rugcheck.xyz/tokens/" + m);
                }
            });
            ctxRadar.Items.Add("🎯 Abrir en GMGN", null, (s, e) =>
            {
                if (_gridRadar.SelectedRows.Count > 0)
                {
                    string m = _gridRadar.SelectedRows[0].Cells[2].Value.ToString();
                    Process.Start("https://gmgn.ai/sol/token/" + m);
                }
            });
            _gridRadar.ContextMenuStrip = ctxRadar;

            tab.Controls.Add(_gridRadar);
        }

        private void InitializePipelineTab(TabPage tab)
        {
            Panel topFunnel = new Panel();
            topFunnel.Dock = DockStyle.Top;
            topFunnel.Height = 44;
            topFunnel.BackColor = Color.FromArgb(16, 21, 30);
            topFunnel.Padding = new Padding(12, 10, 12, 10);

            _lblFunnelInfo = new Label();
            _lblFunnelInfo.Text = "📊 Embudo de Decisión: Vistos: 0 · Vetos: 0 · Neutros: 0 · Admitidos: 0 · Ejecutados: 0";
            _lblFunnelInfo.Font = new Font("Segoe UI", 9.5f, FontStyle.Bold);
            _lblFunnelInfo.ForeColor = Color.FromArgb(0, 229, 255);
            _lblFunnelInfo.AutoSize = true;
            _lblFunnelInfo.Location = new Point(12, 11);
            topFunnel.Controls.Add(_lblFunnelInfo);

            tab.Controls.Add(topFunnel);

            SplitContainer split = new SplitContainer();
            split.Dock = DockStyle.Fill;
            split.Orientation = Orientation.Horizontal;
            split.SplitterDistance = 330;
            split.BackColor = Color.FromArgb(26, 35, 48);

            _gridPipeline = CreateStyledGrid();
            _gridPipeline.Dock = DockStyle.Fill;
            _gridPipeline.Columns.Add("hora", "Hora");
            _gridPipeline.Columns.Add("symbol", "Símbolo");
            _gridPipeline.Columns.Add("mint", "Mint");
            _gridPipeline.Columns.Add("verdict", "Veredicto");
            _gridPipeline.Columns.Add("stage", "Etapa Veto");
            _gridPipeline.Columns.Add("executed", "Ejecutado");

            _gridPipeline.Columns[0].Width = 70;
            _gridPipeline.Columns[1].Width = 90;
            _gridPipeline.Columns[2].Width = 240;
            _gridPipeline.Columns[3].Width = 100;
            _gridPipeline.Columns[4].Width = 160;
            _gridPipeline.Columns[5].AutoSizeMode = DataGridViewAutoSizeColumnMode.Fill;

            _gridPipeline.SelectionChanged += (s, e) => DisplaySelectedPipelineStages();
            split.Panel1.Controls.Add(_gridPipeline);

            _txtPipelineStages = new RichTextBox();
            _txtPipelineStages.Dock = DockStyle.Fill;
            _txtPipelineStages.ReadOnly = true;
            _txtPipelineStages.BackColor = Color.FromArgb(8, 12, 18);
            _txtPipelineStages.ForeColor = Color.FromArgb(226, 232, 240);
            _txtPipelineStages.Font = new Font("Consolas", 9.25f);
            _txtPipelineStages.BorderStyle = BorderStyle.None;
            split.Panel2.Controls.Add(_txtPipelineStages);

            tab.Controls.Add(split);
        }

        private void InitializePositionsTab(TabPage tab)
        {
            _gridPositions = CreateStyledGrid();
            _gridPositions.Dock = DockStyle.Fill;
            _gridPositions.Columns.Add("symbol", "Símbolo");
            _gridPositions.Columns.Add("mint", "Mint");
            _gridPositions.Columns.Add("entry_mc", "MC Entrada ($)");
            _gridPositions.Columns.Add("curr_mc", "MC Actual ($)");
            _gridPositions.Columns.Add("pnl_pct", "PnL %");
            _gridPositions.Columns.Add("pnl_usd", "PnL ($)");
            _gridPositions.Columns.Add("status", "Estado / Disparadores");
            _gridPositions.Columns.Add("duration", "Duración");

            _gridPositions.Columns[0].Width = 90;
            _gridPositions.Columns[1].Width = 230;
            _gridPositions.Columns[2].Width = 120;
            _gridPositions.Columns[3].Width = 120;
            _gridPositions.Columns[4].Width = 100;
            _gridPositions.Columns[5].Width = 100;
            _gridPositions.Columns[6].Width = 160;
            _gridPositions.Columns[7].AutoSizeMode = DataGridViewAutoSizeColumnMode.Fill;

            ContextMenuStrip ctxPositions = new ContextMenuStrip();
            ctxPositions.Items.Add("🚨 Cerrar Posición Ahora", null, (s, e) =>
            {
                if (_gridPositions.SelectedRows.Count > 0)
                {
                    string m = _gridPositions.SelectedRows[0].Cells[1].Value.ToString();
                    CloseSinglePosition(m);
                }
            });
            ctxPositions.Items.Add("📋 Copiar Mint", null, (s, e) =>
            {
                if (_gridPositions.SelectedRows.Count > 0)
                {
                    string m = _gridPositions.SelectedRows[0].Cells[1].Value.ToString();
                    Clipboard.SetText(m);
                    ShowToast("📋 Mint copiado: " + m);
                }
            });
            ctxPositions.Items.Add("📈 Abrir en DexScreener", null, (s, e) =>
            {
                if (_gridPositions.SelectedRows.Count > 0)
                {
                    string m = _gridPositions.SelectedRows[0].Cells[1].Value.ToString();
                    Process.Start("https://dexscreener.com/solana/" + m);
                }
            });
            _gridPositions.ContextMenuStrip = ctxPositions;

            tab.Controls.Add(_gridPositions);
        }

        private void InitializeShadowTab(TabPage tab)
        {
            _gridShadow = CreateStyledGrid();
            _gridShadow.Dock = DockStyle.Fill;
            _gridShadow.Columns.Add("symbol", "Símbolo");
            _gridShadow.Columns.Add("mint", "Mint");
            _gridShadow.Columns.Add("veto_mc", "MC al Vetar ($)");
            _gridShadow.Columns.Add("curr_mc", "MC Actual ($)");
            _gridShadow.Columns.Add("counter_pnl", "PnL Contrafactual %");
            _gridShadow.Columns.Add("verdict", "Resultado");
            _gridShadow.Columns.Add("reason", "Regla de Rechazo Cuantitativa");

            _gridShadow.Columns[0].Width = 90;
            _gridShadow.Columns[1].Width = 230;
            _gridShadow.Columns[2].Width = 120;
            _gridShadow.Columns[3].Width = 120;
            _gridShadow.Columns[4].Width = 130;
            _gridShadow.Columns[5].Width = 110;
            _gridShadow.Columns[6].AutoSizeMode = DataGridViewAutoSizeColumnMode.Fill;

            tab.Controls.Add(_gridShadow);
        }

        private void InitializeLogsTab(TabPage tab)
        {
            Panel actionPanel = new Panel();
            actionPanel.Dock = DockStyle.Top;
            actionPanel.Height = 40;
            actionPanel.BackColor = Color.FromArgb(16, 21, 30);
            actionPanel.Padding = new Padding(10, 6, 10, 6);

            Button btnClear = new Button();
            btnClear.Text = "🧹 Limpiar Consola";
            btnClear.Size = new Size(130, 28);
            btnClear.Location = new Point(10, 6);
            btnClear.FlatStyle = FlatStyle.Flat;
            btnClear.BackColor = Color.FromArgb(33, 44, 61);
            btnClear.ForeColor = Color.White;
            btnClear.Cursor = Cursors.Hand;
            btnClear.Click += (s, e) => _txtLogs.Clear();
            actionPanel.Controls.Add(btnClear);

            Label logInfo = new Label();
            logInfo.Text = "Transmisión en tiempo real de eventos on-chain, filtros de seguridad y motor de autoaprendizaje.";
            logInfo.ForeColor = Color.FromArgb(148, 163, 184);
            logInfo.Font = new Font("Segoe UI", 8.5f, FontStyle.Italic);
            logInfo.AutoSize = true;
            logInfo.Location = new Point(155, 12);
            actionPanel.Controls.Add(logInfo);

            tab.Controls.Add(actionPanel);

            _txtLogs = new RichTextBox();
            _txtLogs.Dock = DockStyle.Fill;
            _txtLogs.ReadOnly = true;
            _txtLogs.BackColor = Color.FromArgb(8, 12, 18);
            _txtLogs.ForeColor = Color.FromArgb(126, 231, 135);
            _txtLogs.Font = new Font("Consolas", 9.5f);
            _txtLogs.BorderStyle = BorderStyle.None;
            tab.Controls.Add(_txtLogs);

            AppendLog("=== PUMPSCOPE WIN32 HIGH-FREQUENCY MONITOR & EVOLUTION HUB ===", Color.FromArgb(88, 166, 255));
            AppendLog("[SISTEMA] Conectando a feeds DEX en vivo (Pump.fun / Raydium / DexScreener).", Color.FromArgb(0, 229, 255));
            AppendLog("[GROUND TRUTH] 5,021 cierres · WR 12.55% · PF 0.269 · SL real -33.49% · MODO PAPER FORWARD.", Color.FromArgb(245, 158, 11));
        }

        private void InitializeAiTab(TabPage tab)
        {
            Panel topBar = new Panel();
            topBar.Dock = DockStyle.Top;
            topBar.Height = 85;
            topBar.BackColor = Color.FromArgb(16, 21, 30);
            topBar.Padding = new Padding(12, 8, 12, 8);

            Label title = new Label();
            title.Text = "🤖 Terminal Cuántica & Agente Autónomo (OpenRouter / DeepSeek / Gemini)";
            title.Font = new Font("Segoe UI", 9.5f, FontStyle.Bold);
            title.ForeColor = Color.FromArgb(168, 85, 247);
            title.AutoSize = true;
            title.Location = new Point(12, 8);
            topBar.Controls.Add(title);

            // Quick Prompt Buttons
            int qx = 12;
            int qy = 42;
            string[] quickCmds = new string[] { "/evolve", "/shadow", "/filter", "/status" };
            foreach (string cmd in quickCmds)
            {
                Button btnQ = new Button();
                btnQ.Text = cmd;
                btnQ.Size = new Size(80, 28);
                btnQ.Location = new Point(qx, qy);
                btnQ.FlatStyle = FlatStyle.Flat;
                btnQ.BackColor = Color.FromArgb(33, 44, 61);
                btnQ.ForeColor = Color.FromArgb(0, 229, 255);
                btnQ.Cursor = Cursors.Hand;
                string capture = cmd;
                btnQ.Click += (s, e) => { _txtPrompt.Text = capture; SendAiPrompt(); };
                topBar.Controls.Add(btnQ);
                qx += 90;
            }

            _btnTriggerEvolve = new Button();
            _btnTriggerEvolve.Text = "⚡ Disparar Auto-Evolve Ahora";
            _btnTriggerEvolve.Size = new Size(200, 28);
            _btnTriggerEvolve.Location = new Point(qx + 10, qy);
            _btnTriggerEvolve.FlatStyle = FlatStyle.Flat;
            _btnTriggerEvolve.BackColor = Color.FromArgb(35, 134, 54);
            _btnTriggerEvolve.ForeColor = Color.White;
            _btnTriggerEvolve.Cursor = Cursors.Hand;
            _btnTriggerEvolve.Click += (s, e) => TriggerManualEvolution();
            topBar.Controls.Add(_btnTriggerEvolve);

            tab.Controls.Add(topBar);

            Panel bottomBar = new Panel();
            bottomBar.Dock = DockStyle.Bottom;
            bottomBar.Height = 55;
            bottomBar.BackColor = Color.FromArgb(16, 21, 30);
            bottomBar.Padding = new Padding(12, 10, 12, 10);

            _txtPrompt = new TextBox();
            _txtPrompt.Location = new Point(12, 12);
            _txtPrompt.Size = new Size(800, 30);
            _txtPrompt.BackColor = Color.FromArgb(10, 13, 20);
            _txtPrompt.ForeColor = Color.White;
            _txtPrompt.BorderStyle = BorderStyle.FixedSingle;
            _txtPrompt.Font = new Font("Segoe UI", 10.5f);
            _txtPrompt.Text = "/evolve";
            _txtPrompt.KeyDown += (s, e) => { if (e.KeyCode == Keys.Enter) { e.SuppressKeyPress = true; SendAiPrompt(); } };
            bottomBar.Controls.Add(_txtPrompt);

            _btnSendAi = new Button();
            _btnSendAi.Text = "Enviar Prompt";
            _btnSendAi.Size = new Size(140, 30);
            _btnSendAi.Location = new Point(825, 12);
            _btnSendAi.FlatStyle = FlatStyle.Flat;
            _btnSendAi.BackColor = Color.FromArgb(137, 87, 229);
            _btnSendAi.ForeColor = Color.White;
            _btnSendAi.Cursor = Cursors.Hand;
            _btnSendAi.Click += (s, e) => SendAiPrompt();
            bottomBar.Controls.Add(_btnSendAi);

            tab.Controls.Add(bottomBar);

            _txtAiResponse = new RichTextBox();
            _txtAiResponse.Dock = DockStyle.Fill;
            _txtAiResponse.ReadOnly = true;
            _txtAiResponse.BackColor = Color.FromArgb(8, 12, 18);
            _txtAiResponse.ForeColor = Color.FromArgb(226, 232, 240);
            _txtAiResponse.Font = new Font("Consolas", 10f);
            _txtAiResponse.BorderStyle = BorderStyle.None;
            tab.Controls.Add(_txtAiResponse);
        }

        private DataGridView CreateStyledGrid()
        {
            DataGridView grid = new DataGridView();
            EnableDoubleBuffering(grid);
            grid.BackgroundColor = Color.FromArgb(10, 13, 20);
            grid.BorderStyle = BorderStyle.None;
            grid.CellBorderStyle = DataGridViewCellBorderStyle.SingleHorizontal;
            grid.GridColor = Color.FromArgb(26, 35, 48);

            grid.EnableHeadersVisualStyles = false;
            grid.ColumnHeadersHeightSizeMode = DataGridViewColumnHeadersHeightSizeMode.DisableResizing;
            grid.ColumnHeadersHeight = 32;
            grid.ColumnHeadersDefaultCellStyle.BackColor = Color.FromArgb(18, 24, 37);
            grid.ColumnHeadersDefaultCellStyle.ForeColor = Color.FromArgb(88, 166, 255);
            grid.ColumnHeadersDefaultCellStyle.Font = new Font("Segoe UI", 8.75f, FontStyle.Bold);

            grid.RowTemplate.Height = 28;
            grid.RowsDefaultCellStyle.BackColor = Color.FromArgb(10, 13, 20);
            grid.RowsDefaultCellStyle.ForeColor = Color.FromArgb(226, 232, 240);
            grid.RowsDefaultCellStyle.Font = new Font("Segoe UI", 8.75f);
            grid.RowsDefaultCellStyle.SelectionBackColor = Color.FromArgb(33, 44, 61);
            grid.RowsDefaultCellStyle.SelectionForeColor = Color.White;

            grid.AlternatingRowsDefaultCellStyle.BackColor = Color.FromArgb(14, 18, 26);
            grid.AlternatingRowsDefaultCellStyle.ForeColor = Color.FromArgb(226, 232, 240);
            grid.AlternatingRowsDefaultCellStyle.SelectionBackColor = Color.FromArgb(33, 44, 61);

            grid.RowHeadersVisible = false;
            grid.ReadOnly = true;
            grid.AllowUserToAddRows = false;
            grid.AllowUserToDeleteRows = false;
            grid.AllowUserToResizeRows = false;
            grid.SelectionMode = DataGridViewSelectionMode.FullRowSelect;
            grid.MultiSelect = false;

            return grid;
        }

        public void AppendLog(string message, Color color)
        {
            if (this.InvokeRequired)
            {
                this.BeginInvoke(new Action<string, Color>(AppendLog), message, color);
                return;
            }
            string timestamp = DateTime.Now.ToString("HH:mm:ss");
            _txtLogs.SelectionStart = _txtLogs.TextLength;
            _txtLogs.SelectionLength = 0;
            _txtLogs.SelectionColor = Color.FromArgb(100, 116, 139);
            _txtLogs.AppendText(string.Format("[{0}] ", timestamp));

            _txtLogs.SelectionStart = _txtLogs.TextLength;
            _txtLogs.SelectionLength = 0;
            _txtLogs.SelectionColor = color;
            _txtLogs.AppendText(message + "\r\n");
            _txtLogs.ScrollToCaret();
        }

        private void CheckBackendHealth()
        {
            ThreadPool.QueueUserWorkItem((state) =>
            {
                bool alive = PingBackend();
                this.BeginInvoke(new Action(() =>
                {
                    if (alive)
                    {
                        _lblStatus.Text = "● SERVIDOR CONECTADO & LOOP ACTIVO (127.0.0.1:8000) · DEX REAL";
                        _lblStatus.ForeColor = Color.FromArgb(0, 255, 170);
                        _btnToggleServer.Text = "■ Detener Server";
                        _btnToggleServer.BackColor = Color.FromArgb(218, 54, 51);
                        AppendLog("[BACKEND] Servidor local 127.0.0.1:8000 en línea y operativo.", Color.FromArgb(0, 255, 170));
                        PollAllData();
                    }
                    else
                    {
                        _lblStatus.Text = "○ Servidor detenido (Iniciando proceso automáticamente...)";
                        _lblStatus.ForeColor = Color.FromArgb(245, 158, 11);
                        _btnToggleServer.Text = "▶ Iniciar Server";
                        _btnToggleServer.BackColor = Color.FromArgb(35, 134, 54);
                        StartServerProcess();
                    }
                }));
            });
        }

        private bool PingBackend()
        {
            try
            {
                HttpWebRequest req = (HttpWebRequest)WebRequest.Create(_baseUrl + "/api/bot/status");
                req.Timeout = 1500;
                req.Method = "GET";
                using (HttpWebResponse resp = (HttpWebResponse)req.GetResponse())
                {
                    return resp.StatusCode == HttpStatusCode.OK;
                }
            }
            catch
            {
                return false;
            }
        }

        private void PollAllData()
        {
            ThreadPool.QueueUserWorkItem((state) =>
            {
                try
                {
                    string botJson = HttpGet(_baseUrl + "/api/bot/status");
                    string tokensJson = HttpGet(_baseUrl + "/api/gmgn/tokens");
                    string pipelineJson = "";
                    try
                    {
                        pipelineJson = HttpGet(_baseUrl + "/api/pipeline/live?limit=30");
                    }
                    catch { pipelineJson = ""; }
                    string logsJson = "";
                    try
                    {
                        logsJson = HttpGet(_baseUrl + "/api/logs?since=" + _lastLogId.ToString(CultureInfo.InvariantCulture));
                    }
                    catch { logsJson = ""; }

                    this.BeginInvoke(new Action(() =>
                    {
                        UpdateAllUi(botJson, tokensJson, logsJson, pipelineJson);
                    }));
                }
                catch (Exception ex)
                {
                    this.BeginInvoke(new Action(() =>
                    {
                        _lblStatus.Text = "○ Servidor sin respuesta (reintentando conexión...)";
                        _lblStatus.ForeColor = Color.FromArgb(245, 158, 11);
                        AppendLog("[WARN] Error de comunicación: " + ex.Message, Color.FromArgb(245, 158, 11));
                    }));
                }
            });
        }

        private void UpdateAllUi(string botJson, string tokensJson, string logsJson, string pipelineJson)
        {
            if (string.IsNullOrEmpty(botJson)) return;

            try
            {
                Dictionary<string, object> botData = _serializer.Deserialize<Dictionary<string, object>>(botJson);
                // Fuente única del exe: el bloque canónico `dashboard` (contrato P0.1).
                Dictionary<string, object> dash = GetDict(botData, "dashboard");
                Dictionary<string, object> wallet = GetDict(dash, "paper_wallet");
                Dictionary<string, object> dashEv = GetDict(dash, "evolution");
                Dictionary<string, object> runtime = GetDict(dash, "runtime");
                bool contractOk = dash != null && wallet != null && runtime != null;

                bool stale = contractOk && GetBool(runtime, "stale");
                bool loopAlive = contractOk && GetBool(runtime, "loop_alive");

                if (contractOk)
                {
                    if (stale)
                    {
                        _lblStatus.Text = "◐ SERVIDOR CONECTADO · DATOS VENCIDOS (STALE / sin tick reciente)";
                        _lblStatus.ForeColor = Color.FromArgb(245, 158, 11);
                    }
                    else if (!loopAlive)
                    {
                        _lblStatus.Text = "◐ SERVIDOR CONECTADO · LOOP AUTÓNOMO DETENIDO (sin decisiones nuevas)";
                        _lblStatus.ForeColor = Color.FromArgb(245, 158, 11);
                    }
                    else
                    {
                        _lblStatus.Text = "● SERVIDOR CONECTADO & LOOP ACTIVO (127.0.0.1:8000) · DEX REAL";
                        _lblStatus.ForeColor = Color.FromArgb(0, 255, 170);
                    }
                }
                else
                {
                    _lblStatus.Text = "○ Servidor responde sin bloque `dashboard` (contrato desalineado: actualice el server)";
                    _lblStatus.ForeColor = Color.FromArgb(245, 158, 11);
                }

                // 1. Paper wallet canónica (nunca un default pintado como métrica)
                if (wallet != null)
                {
                    _lblBalanceVal.Text = HasVal(wallet, "equity_usd")
                        ? string.Format(CultureInfo.InvariantCulture, "${0:N2} USD", GetNum(wallet, "equity_usd", 0))
                        : ND;

                    if (HasVal(wallet, "pnl_usd"))
                    {
                        double pnlUsd = GetNum(wallet, "pnl_usd", 0);
                        string expTxt = HasVal(wallet, "exposure_pct")
                            ? GetNum(wallet, "exposure_pct", 0).ToString("F1", CultureInfo.InvariantCulture) + "%"
                            : "N/D";
                        if (GetStr(wallet, "sol_price_source", "") != "live")
                            expTxt += " (SOL asumido)";
                        _lblBalanceSub.Text = string.Format(CultureInfo.InvariantCulture,
                            "PnL: {0}{1:F2} USD ({2} SOL) · {3} cerradas · exp {4}",
                            pnlUsd >= 0 ? "+$" : "-$", Math.Abs(pnlUsd),
                            HasVal(wallet, "pnl_sol") ? GetNum(wallet, "pnl_sol", 0).ToString("F3", CultureInfo.InvariantCulture) : "N/D",
                            HasVal(wallet, "closed_count") ? GetNum(wallet, "closed_count", 0).ToString("F0", CultureInfo.InvariantCulture) : "N/D",
                            expTxt);
                    }
                    else
                    {
                        _lblBalanceSub.Text = "PnL: " + ND;
                    }
                }
                else
                {
                    _lblBalanceVal.Text = ND;
                    _lblBalanceSub.Text = "Bloque `dashboard.paper_wallet` ausente";
                }

                // 2. Positions (canónico: dashboard.paper_wallet.open_count + dashboard.open_positions)
                if (wallet != null)
                {
                    _lblPositionsVal.Text = HasVal(wallet, "open_count")
                        ? string.Format(CultureInfo.InvariantCulture, "{0} activas", GetNum(wallet, "open_count", 0).ToString("F0", CultureInfo.InvariantCulture))
                        : ND;
                    _lblPositionsSub.Text = "TP1 +50% | BE +25% | SL -33.49%";
                }

                int prevPosIdx = GetSelectedRowIndex(_gridPositions);
                int prevPosScroll = _gridPositions.FirstDisplayedScrollingRowIndex;
                _gridPositions.Rows.Clear();
                ArrayList openPos = GetList(dash, "open_positions");
                if (openPos != null && openPos.Count > 0)
                {
                    foreach (object item in openPos)
                    {
                        Dictionary<string, object> pos = item as Dictionary<string, object>;
                        if (pos == null) continue;

                        string sym = GetStr(pos, "symbol", "N/A");
                        string mint = GetStr(pos, "mint", "");
                        double entMc = GetNum(pos, "entry_mc", 0);
                        double curMc = GetNum(pos, "current_mc", 0);
                        double pnl = GetNum(pos, "pnl_pct", 0);
                        double pnlU = GetNum(pos, "pnl_usd", 0);
                        bool tp1 = GetBool(pos, "tp1_hit");
                        bool be = GetBool(pos, "be_hit");

                        string statusStr = tp1 ? "TP1 (+50%) Tomado" : (be ? "Break-Even (+25%)" : "Monitoreando");
                        int rIdx = _gridPositions.Rows.Add(sym, mint, string.Format(CultureInfo.InvariantCulture, "${0:N0}", entMc),
                                                           string.Format(CultureInfo.InvariantCulture, "${0:N0}", curMc),
                                                           string.Format(CultureInfo.InvariantCulture, "{0:+0.0;-0.0}%", pnl),
                                                           string.Format(CultureInfo.InvariantCulture, "${0:F2}", pnlU), statusStr,
                                                           GetStr(pos, "status", "OPEN"));
                        if (pnl > 0)
                            _gridPositions.Rows[rIdx].Cells[4].Style.ForeColor = Color.FromArgb(0, 255, 170);
                        else if (pnl < 0)
                            _gridPositions.Rows[rIdx].Cells[4].Style.ForeColor = Color.FromArgb(255, 51, 102);
                    }
                }
                RestoreSelection(_gridPositions, prevPosIdx, prevPosScroll);

                // 3. Shadow Vetos (canónico: dashboard.evolution + dashboard.shadow_vetos)
                if (dashEv != null)
                {
                    if (HasVal(dashEv, "true_saves") || HasVal(dashEv, "false_vetos"))
                    {
                        int trueSaves = (int)GetNum(dashEv, "true_saves", 0);
                        int falseVetos = (int)GetNum(dashEv, "false_vetos", 0);
                        _lblShadowVal.Text = string.Format("{0} True Saves · {1} Falsos Vetos", trueSaves, falseVetos);
                        string avoided = HasVal(dashEv, "avoided_loss_pct")
                            ? GetNum(dashEv, "avoided_loss_pct", 0).ToString("F1", CultureInfo.InvariantCulture) + "% de pérdida evitada (suma absoluta, contrafactual)"
                            : "Pérdida evitada: " + ND;
                        _lblShadowSub.Text = avoided;
                        int totalTracked = trueSaves + falseVetos;
                        if (_lastTrackedShadow != -1 && totalTracked > _lastTrackedShadow)
                        {
                            AppendLog(string.Format("[SHADOW] Nuevo token evaluado en el ledger de vetos. Total: {0}", totalTracked), Color.FromArgb(168, 85, 247));
                        }
                        _lastTrackedShadow = totalTracked;
                    }
                    else
                    {
                        _lblShadowVal.Text = ND;
                        _lblShadowSub.Text = "Shadow ledger sin evaluaciones todavía";
                    }

                    if (HasVal(dashEv, "evolutions_count"))
                    {
                        _lblEvolutionVal.Text = string.Format(CultureInfo.InvariantCulture, "{0} evoluciones de umbrales",
                                                              GetNum(dashEv, "evolutions_count", 0).ToString("F0", CultureInfo.InvariantCulture));
                    }
                    else
                    {
                        _lblEvolutionVal.Text = ND;
                    }

                    Dictionary<string, object> lastRule = GetDict(dashEv, "last_rule_change");
                    if (lastRule != null && HasVal(lastRule, "rule"))
                    {
                        _lblEvolutionSub.Text = string.Format("Última regla: {0}  {1} → {2}",
                            GetStr(lastRule, "rule", "?"), GetStr(lastRule, "old", "?"), GetStr(lastRule, "new", "?"));
                    }
                    else
                    {
                        _lblEvolutionSub.Text = "Sin cambio de regla registrado";
                    }

                    // Update Shadow Grid (lista canónica del dashboard)
                    int prevShIdx = GetSelectedRowIndex(_gridShadow);
                    int prevShScroll = _gridShadow.FirstDisplayedScrollingRowIndex;
                    _gridShadow.Rows.Clear();
                    ArrayList shadowList = GetList(dash, "shadow_vetos");
                    if (shadowList != null)
                    {
                        foreach (object item in shadowList)
                        {
                            Dictionary<string, object> sv = item as Dictionary<string, object>;
                            if (sv == null) continue;

                            string sym = GetStr(sv, "symbol", "N/A");
                            string mint = GetStr(sv, "mint", "");
                            double vMc = GetNum(sv, "entry_mc_usd", 0);
                            double cMc = GetNum(sv, "current_mc_usd", 0);
                            double cPnl = GetNum(sv, "counterfactual_pnl_pct", 0);
                            string verd = GetStr(sv, "verdict", "PENDING");
                            string reason = GetStr(sv, "veto_reason", "");

                            int rIdx = _gridShadow.Rows.Add(sym, mint, string.Format(CultureInfo.InvariantCulture, "${0:N0}", vMc),
                                                            string.Format(CultureInfo.InvariantCulture, "${0:N0}", cMc),
                                                            string.Format(CultureInfo.InvariantCulture, "{0:+0.0;-0.0}%", cPnl), verd, reason);
                            if (verd == "TRUE_SAVE")
                                _gridShadow.Rows[rIdx].Cells[5].Style.ForeColor = Color.FromArgb(0, 255, 170);
                            else if (verd == "FALSE_VETO")
                                _gridShadow.Rows[rIdx].Cells[5].Style.ForeColor = Color.FromArgb(245, 158, 11);
                        }
                    }
                    RestoreSelection(_gridShadow, prevShIdx, prevShScroll);
                }
                else
                {
                    _lblShadowVal.Text = ND;
                    _lblShadowSub.Text = "Bloque `dashboard.evolution` ausente";
                    _lblEvolutionVal.Text = ND;
                    _lblEvolutionSub.Text = "Bloque `dashboard.evolution` ausente";
                }

                // 4. Update Radar Tokens (contrato propio de /api/gmgn/tokens)
                if (!string.IsNullOrEmpty(tokensJson))
                {
                    Dictionary<string, object> tokensData = _serializer.Deserialize<Dictionary<string, object>>(tokensJson);
                    ArrayList tokensList = GetList(tokensData, "tokens");
                    if (tokensList != null)
                    {
                        int prevRadIdx = GetSelectedRowIndex(_gridRadar);
                        int prevRadScroll = _gridRadar.FirstDisplayedScrollingRowIndex;
                        _gridRadar.Rows.Clear();
                        string timeNow = DateTime.Now.ToString("HH:mm:ss");
                        foreach (object item in tokensList)
                        {
                            Dictionary<string, object> t = item as Dictionary<string, object>;
                            if (t == null) continue;

                            string sym = GetStr(t, "symbol", "N/A");
                            string mint = GetStr(t, "mint", "");
                            double mc = GetNum(t, "market_cap_usd", 0);
                            double liq = GetNum(t, "liquidity_usd", 0);
                            string act = GetStr(t, "verdict", "N/D");
                            string reason = GetStr(t, "veto_reason", "");

                            if (string.IsNullOrEmpty(reason))
                                reason = GetBool(t, "metrics_known") ? "Aprobado por matriz de filtros cuantitativos" : "N/D — métricas no verificadas (metrics_known=false)";

                            int rIdx = _gridRadar.Rows.Add(timeNow, sym, mint, string.Format(CultureInfo.InvariantCulture, "${0:N0}", mc),
                                                           string.Format(CultureInfo.InvariantCulture, "${0:N0}", liq), act, reason);

                            if (act == "STRONG_BUY" || act == "SPECULATIVE_BUY" || act == "BUY")
                            {
                                _gridRadar.Rows[rIdx].Cells[5].Style.ForeColor = Color.FromArgb(0, 255, 170);
                                _gridRadar.Rows[rIdx].Cells[5].Style.Font = new Font("Segoe UI", 9f, FontStyle.Bold);
                            }
                            else
                            {
                                _gridRadar.Rows[rIdx].Cells[5].Style.ForeColor = Color.FromArgb(255, 51, 102);
                            }
                        }
                        RestoreSelection(_gridRadar, prevRadIdx, prevRadScroll);
                    }
                }

                // 5. Update Pipeline & Funnel (contrato propio de /api/pipeline/live)
                if (!string.IsNullOrEmpty(pipelineJson))
                {
                    try
                    {
                        Dictionary<string, object> pipeData = _serializer.Deserialize<Dictionary<string, object>>(pipelineJson);
                        Dictionary<string, object> funnel = GetDict(pipeData, "funnel");
                        if (funnel != null && _lblFunnelInfo != null)
                        {
                            double seen = GetNum(funnel, "seen", 0);
                            double vetos = GetNum(funnel, "veto", 0);
                            double neutrals = GetNum(funnel, "neutral", 0);
                            double admits = GetNum(funnel, "admitted", 0);
                            double execs = GetNum(funnel, "executed", 0);
                            _lblFunnelInfo.Text = string.Format(CultureInfo.InvariantCulture,
                                "📊 Embudo de Decisión: Vistos: {0:F0} · Vetos: {1:F0} · Neutros: {2:F0} · Admitidos: {3:F0} · Ejecutados: {4:F0}",
                                seen, vetos, neutrals, admits, execs);
                        }

                        ArrayList tracesList = GetList(pipeData, "traces");
                        if (tracesList != null && _gridPipeline != null)
                        {
                            _cachedPipelineTraces = tracesList;
                            int prevPipeIdx = GetSelectedRowIndex(_gridPipeline);
                            int prevPipeScroll = _gridPipeline.FirstDisplayedScrollingRowIndex;
                            _gridPipeline.Rows.Clear();
                            string timeNow = DateTime.Now.ToString("HH:mm:ss");
                            foreach (object item in tracesList)
                            {
                                Dictionary<string, object> tr = item as Dictionary<string, object>;
                                if (tr == null) continue;
                                string sym = GetStr(tr, "symbol", "N/A");
                                string mint = GetStr(tr, "mint", "");
                                string verd = GetStr(tr, "verdict", "N/D");
                                string vStage = GetStr(tr, "veto_stage", "—");
                                bool exec = GetBool(tr, "executed");

                                int rIdx = _gridPipeline.Rows.Add(timeNow, sym, mint, verd, vStage, exec ? "SÍ (Paper Order)" : "NO");
                                if (verd == "ADMIT")
                                {
                                    _gridPipeline.Rows[rIdx].Cells[3].Style.ForeColor = Color.FromArgb(0, 255, 170);
                                    _gridPipeline.Rows[rIdx].Cells[3].Style.Font = new Font("Segoe UI", 9f, FontStyle.Bold);
                                }
                                else if (verd == "VETO")
                                {
                                    _gridPipeline.Rows[rIdx].Cells[3].Style.ForeColor = Color.FromArgb(255, 51, 102);
                                }
                                else
                                {
                                    _gridPipeline.Rows[rIdx].Cells[3].Style.ForeColor = Color.FromArgb(245, 158, 11);
                                }
                            }
                            RestoreSelection(_gridPipeline, prevPipeIdx, prevPipeScroll);
                        }
                    }
                    catch { }
                }

                // 6. Feed Health (canónico: dashboard.runtime)
                if (runtime != null && HasVal(runtime, "feed_source"))
                {
                    string src = GetStr(runtime, "feed_source", "N/D");
                    string age = HasVal(runtime, "feed_age_s")
                        ? GetNum(runtime, "feed_age_s", 0).ToString("F1", CultureInfo.InvariantCulture) + "s"
                        : "N/D";
                    _statusFeed.Text = string.Format("🌐 DEX Feed: {0} · último poll hace {1}{2}", src, age, stale ? " · STALE" : "");
                    _statusFeed.ForeColor = stale ? Color.FromArgb(245, 158, 11) : Color.FromArgb(0, 229, 255);
                }
                else
                {
                    _statusFeed.Text = "🌐 DEX Feed: " + ND;
                    _statusFeed.ForeColor = Color.FromArgb(245, 158, 11);
                }

                // 7. Log en vivo desde /api/logs (mismo stream que la UI web vía SSE)
                AppendRemoteLogs(logsJson);

                _statusRefresh.Text = string.Format("⏱ Refrescado: {0}", DateTime.Now.ToString("HH:mm:ss"));
            }
            catch (Exception ex)
            {
                AppendLog("[UI PARSE ERROR] " + ex.Message, Color.FromArgb(255, 51, 102));
            }
        }

        private void DisplaySelectedPipelineStages()
        {
            if (_gridPipeline == null || _txtPipelineStages == null || _cachedPipelineTraces == null) return;
            if (_gridPipeline.SelectedRows.Count == 0) return;

            int idx = _gridPipeline.SelectedRows[0].Index;
            if (idx < 0 || idx >= _cachedPipelineTraces.Count) return;

            Dictionary<string, object> trace = _cachedPipelineTraces[idx] as Dictionary<string, object>;
            if (trace == null) return;

            string sym = GetStr(trace, "symbol", "N/A");
            string mint = GetStr(trace, "mint", "");
            string verd = GetStr(trace, "verdict", "N/D");
            string vStage = GetStr(trace, "veto_stage", "—");

            StringBuilder sb = new StringBuilder();
            sb.AppendLine(string.Format("=== TRAZA COMPLETA DE DECISIÓN: {0} ({1}) ===", sym, mint));
            sb.AppendLine(string.Format("Veredicto Final: {0}  |  Etapa de Veto: {1}\r\n", verd, vStage));

            ArrayList stages = GetList(trace, "stages");
            if (stages != null && stages.Count > 0)
            {
                foreach (object stgObj in stages)
                {
                    Dictionary<string, object> stg = stgObj as Dictionary<string, object>;
                    if (stg == null) continue;

                    string sName = GetStr(stg, "stage", "?");
                    string sVerd = GetStr(stg, "verdict", "?");
                    string sReason = GetStr(stg, "reason", "");
                    string sKind = GetStr(stg, "source_kind", "");
                    string op = GetStr(stg, "operator", "");

                    string line = string.Format("• [{0}] Veredicto: {1}", sName, sVerd.ToUpperInvariant());
                    if (HasVal(stg, "value"))
                    {
                        line += string.Format(CultureInfo.InvariantCulture, " | Valor: {0:F2}", GetNum(stg, "value", 0));
                    }
                    if (HasVal(stg, "threshold"))
                    {
                        line += string.Format(CultureInfo.InvariantCulture, " {0} Umbral: {1:F2}", op, GetNum(stg, "threshold", 0));
                    }
                    if (!string.IsNullOrEmpty(sReason))
                    {
                        line += string.Format(" | Motivo: {0}", sReason);
                    }
                    if (!string.IsNullOrEmpty(sKind))
                    {
                        line += string.Format(" | Origen: {0}", sKind);
                    }
                    sb.AppendLine(line);
                }
            }
            else
            {
                sb.AppendLine("(Sin desglose detallado de etapas para esta decisión)");
            }

            _txtPipelineStages.Text = sb.ToString();
        }

        private void AppendRemoteLogs(string logsJson)
        {
            if (string.IsNullOrEmpty(logsJson)) return;
            Dictionary<string, object> logsData;
            try
            {
                logsData = _serializer.Deserialize<Dictionary<string, object>>(logsJson);
            }
            catch
            {
                return;
            }
            ArrayList events = GetList(logsData, "events");
            if (events == null || events.Count == 0)
            {
                if (HasVal(logsData, "latest_id"))
                    _lastLogId = (int)GetNum(logsData, "latest_id", _lastLogId);
                return;
            }
            foreach (object item in events)
            {
                Dictionary<string, object> ev = item as Dictionary<string, object>;
                if (ev == null) continue;
                string kind = GetStr(ev, "kind", "event");
                Dictionary<string, object> data = GetDict(ev, "data");
                string detail = data == null ? "" : SummarizeEvent(kind, data);
                Color color = Color.FromArgb(148, 163, 184);
                if (kind == "trade_open") color = Color.FromArgb(0, 229, 255);
                else if (kind == "trade_close")
                {
                    color = Color.FromArgb(0, 255, 170);
                    // System Tray Notification & Audio cue
                    try
                    {
                        if (data != null && _trayIcon != null)
                        {
                            string sym = GetStr(data, "symbol", "Token");
                            string pnl = GetStr(data, "pnl_pct", "0");
                            string exit = GetStr(data, "exit_reason", "EXIT");
                            _trayIcon.ShowBalloonTip(3000, "Trade Cerrado: " + sym,
                                string.Format("PnL: {0}% | Salida: {1}", pnl, exit), ToolTipIcon.Info);
                            System.Media.SystemSounds.Asterisk.Play();
                        }
                    }
                    catch { }
                }
                else if (kind == "evo") color = Color.FromArgb(168, 85, 247);
                else if (kind == "error") color = Color.FromArgb(255, 51, 102);
                else if (kind == "feed") color = Color.FromArgb(245, 158, 11);
                AppendLog(string.Format("[{0}] {1}", kind.ToUpperInvariant(), detail), color);
                if (HasVal(ev, "id"))
                {
                    int evId = (int)GetNum(ev, "id", 0);
                    if (evId > _lastLogId) _lastLogId = evId;
                }
            }
        }

        private string SummarizeEvent(string kind, Dictionary<string, object> data)
        {
            if (kind == "stage")
            {
                if (GetStr(data, "stage", "") == "screen")
                    return string.Format("Pipeline/screen · fuente {0} · {1} tokens ({2} admitidos, {3} vetados)",
                        GetStr(data, "source", "N/D"), GetStr(data, "tokens", "N/D"),
                        GetStr(data, "admitted", "N/D"), GetStr(data, "vetados", "N/D"));
                if (GetStr(data, "stage", "") == "heartbeat")
                    return string.Format("Heartbeat · {0} abiertas · {1} cerradas · auto_bot={2}",
                        GetStr(data, "open_count", "N/D"), GetStr(data, "closed_count", "N/D"), GetStr(data, "auto_bot_enabled", "N/D"));
                return string.Format("Etapa {0}", GetStr(data, "stage", "N/D"));
            }
            if (kind == "trade_open")
                return string.Format("Entrada paper {0} ({1}) @ MC ${2} · {3} SOL",
                    GetStr(data, "symbol", "N/D"), GetStr(data, "mint", ""), GetStr(data, "entry_mc_usd", "N/D"), GetStr(data, "size_sol", "N/D"));
            if (kind == "trade_close")
                return string.Format("Cierre paper {0} · PnL {1}% · salida {2}",
                    GetStr(data, "symbol", "N/D"), GetStr(data, "pnl_pct", "N/D"), GetStr(data, "exit_reason", "N/D"));
            if (kind == "evo")
                return string.Format("Auto-evolución #{0} · regla {1}: {2} → {3}",
                    GetStr(data, "evolutions_count", "N/D"), GetStr(data, "rule", "N/D"), GetStr(data, "old", "N/D"), GetStr(data, "new", "N/D"));
            if (kind == "feed")
                return string.Format("Feed {0} · calls {1} · errors {2} · last_error {3}",
                    GetStr(data, "source", "N/D"), GetStr(data, "calls", "N/D"), GetStr(data, "errors", "N/D"), GetStr(data, "last_error", "none"));
            if (kind == "error")
                return string.Format("ERROR en {0}: {1}", GetStr(data, "stage", "N/D"), GetStr(data, "error", "N/D"));
            return _serializer.Serialize(data);
        }

        private void LaunchStandaloneCockpit()
        {
            string edgePath = @"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe";
            if (!File.Exists(edgePath))
                edgePath = @"C:\Program Files\Microsoft\Edge\Application\msedge.exe";

            string chromePath = @"C:\Program Files\Google\Chrome\Application\chrome.exe";
            if (!File.Exists(chromePath))
                chromePath = @"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe";

            try
            {
                if (File.Exists(edgePath))
                {
                    Process.Start(edgePath, string.Format("--app=\"{0}\" --window-size=1400,900", _baseUrl));
                    AppendLog("[DESKTOP] Cockpit 3D/2D lanzado en ventana nativa Edge App.", Color.FromArgb(0, 229, 255));
                }
                else if (File.Exists(chromePath))
                {
                    Process.Start(chromePath, string.Format("--app=\"{0}\" --window-size=1400,900", _baseUrl));
                    AppendLog("[DESKTOP] Cockpit 3D/2D lanzado en ventana nativa Chrome App.", Color.FromArgb(0, 229, 255));
                }
                else
                {
                    Process.Start(_baseUrl);
                    AppendLog("[DESKTOP] Cockpit abierto en navegador predeterminado.", Color.FromArgb(0, 229, 255));
                }
            }
            catch (Exception ex)
            {
                AppendLog("[ERROR] Al abrir Cockpit: " + ex.Message, Color.FromArgb(255, 51, 102));
            }
        }

        private void InspectSelectedRadarToken()
        {
            if (_gridRadar.SelectedRows.Count > 0)
            {
                object cellVal = _gridRadar.SelectedRows[0].Cells[2].Value;
                if (cellVal != null)
                {
                    string mint = cellVal.ToString();
                    InspectDossier(mint);
                }
            }
            else
            {
                ShowToast("Seleccione un token en la grilla para inspeccionar su dossier.");
            }
        }

        private void InspectDossier(string mint)
        {
            AppendLog("[DOSSIER] Consultando expediente forense on-chain para: " + mint, Color.FromArgb(0, 229, 255));
            ThreadPool.QueueUserWorkItem((state) =>
            {
                try
                {
                    string payload = string.Format("{{\"mint\": \"{0}\"}}", EscapeJson(mint));
                    string json = HttpPost(_baseUrl + "/api/dossier/inspect", payload);
                    Dictionary<string, object> res = _serializer.Deserialize<Dictionary<string, object>>(json);
                    Dictionary<string, object> dos = GetDict(res, "dossier");
                    this.BeginInvoke(new Action(() =>
                    {
                        if (dos != null)
                        {
                            double bundler = GetNum(dos, "estimated_bundler_pct", 0);
                            double wash = GetNum(dos, "wash_trading_score", 0);
                            double devHold = GetNum(dos, "dev_holding_pct", 0);
                            double clones = GetNum(dos, "same_name_count_24h", 0);
                            double devRugs = GetNum(dos, "dev_rug_count", 0);
                            string sym = GetStr(dos, "symbol", "N/A");

                            string report = string.Format(CultureInfo.InvariantCulture,
                                "=== EXPEDIENTE FORENSE ON-CHAIN ===\r\n" +
                                "Símbolo: {0}  |  Mint: {1}\r\n\r\n" +
                                "• Bundler Supply Empaquetado: {2:F1}%\r\n" +
                                "• Wash Trading Score: {3:F1}%\r\n" +
                                "• Dev Balance: {4:F1}%\r\n" +
                                "• Clones Homónimos (24h): {5:F0}\r\n" +
                                "• Rugs Previos del Dev: {6:F0}",
                                sym, mint, bundler, wash, devHold, clones, devRugs);

                            AppendLog(string.Format(CultureInfo.InvariantCulture,
                                "[DOSSIER] {0} · Bundler {1:F1}% · Wash {2:F1}% · Clones {3:F0}", sym, bundler, wash, clones),
                                Color.FromArgb(168, 85, 247));
                            MessageBox.Show(this, report, "Auditoría Forense: " + sym, MessageBoxButtons.OK, MessageBoxIcon.Information);
                        }
                        else
                        {
                            ShowToast("Sin dossier disponible para " + mint);
                        }
                    }));
                }
                catch (Exception ex)
                {
                    this.BeginInvoke(new Action(() =>
                    {
                        ShowToast("Error en dossier: " + ex.Message);
                    }));
                }
            });
        }

        private void OpenSelectedTokenInDex()
        {
            if (_gridRadar.SelectedRows.Count > 0)
            {
                object cellVal = _gridRadar.SelectedRows[0].Cells[2].Value;
                if (cellVal != null)
                {
                    string mint = cellVal.ToString();
                    string url = "https://dexscreener.com/solana/" + mint;
                    try
                    {
                        Process.Start(url);
                        AppendLog("[DEX] Abriendo gráfico en DexScreener: " + url, Color.FromArgb(0, 229, 255));
                    }
                    catch (Exception ex)
                    {
                        AppendLog("[ERROR] " + ex.Message, Color.FromArgb(255, 51, 102));
                    }
                }
            }
        }

        private void ToggleBotSniper()
        {
            ThreadPool.QueueUserWorkItem((state) =>
            {
                try
                {
                    string json = HttpPost(_baseUrl + "/api/bot/toggle", "{}");
                    Dictionary<string, object> res = _serializer.Deserialize<Dictionary<string, object>>(json);
                    bool enabled = GetBool(res, "auto_bot_enabled");
                    this.BeginInvoke(new Action(() =>
                    {
                        _autoBotEnabled = enabled;
                        UpdateBotButtonUi();
                        ShowToast(enabled ? "🟢 Bot Sniper ACTIVADO" : "🔴 Bot Sniper PAUSADO");
                        AppendLog(enabled ? "[BOT] Sniper autónomo activado por operador." : "[BOT] Sniper autónomo pausado por operador.", Color.FromArgb(0, 229, 255));
                    }));
                }
                catch (Exception ex)
                {
                    this.BeginInvoke(new Action(() =>
                    {
                        ShowToast("Error al conmutar bot: " + ex.Message);
                    }));
                }
            });
        }

        private void UpdateBotButtonUi()
        {
            if (_btnToggleBot == null) return;
            if (_autoBotEnabled)
            {
                _btnToggleBot.Text = "🟢 Bot: ACTIVO";
                _btnToggleBot.BackColor = Color.FromArgb(35, 134, 54);
            }
            else
            {
                _btnToggleBot.Text = "🔴 Bot: PAUSADO";
                _btnToggleBot.BackColor = Color.FromArgb(160, 30, 30);
            }
        }

        private void PanicCloseAllPositions()
        {
            DialogResult dr = MessageBox.Show(this,
                "¿CONFIRMA SALIDA DE PÁNICO DE TODAS LAS POSICIONES PAPER?\r\n\r\nSe enviará orden de cierre inmediato con slippage real de salida.",
                "🚨 ALERTA DE PÁNICO", MessageBoxButtons.YesNo, MessageBoxIcon.Warning);
            if (dr != DialogResult.Yes) return;

            ThreadPool.QueueUserWorkItem((state) =>
            {
                try
                {
                    int closed = 0;
                    for (int i = 0; i < _gridPositions.Rows.Count; i++)
                    {
                        object mintVal = _gridPositions.Rows[i].Cells[1].Value;
                        if (mintVal != null)
                        {
                            string mint = mintVal.ToString();
                            string payload = string.Format("{{\"mint\": \"{0}\", \"reason\": \"PANIC_OPERATOR_EXIT\"}}", EscapeJson(mint));
                            try
                            {
                                HttpPost(_baseUrl + "/api/bot/close", payload);
                                closed++;
                            }
                            catch { }
                        }
                    }
                    this.BeginInvoke(new Action(() =>
                    {
                        ShowToast(string.Format(CultureInfo.InvariantCulture, "🚨 Pánico: {0} órdenes enviadas", closed));
                        AppendLog(string.Format(CultureInfo.InvariantCulture, "[PANIC] Salida de pánico ejecutada ({0} órdenes)", closed), Color.FromArgb(255, 51, 102));
                        PollAllData();
                    }));
                }
                catch (Exception ex)
                {
                    this.BeginInvoke(new Action(() =>
                    {
                        ShowToast("Error en panic close: " + ex.Message);
                    }));
                }
            });
        }

        private void CloseSinglePosition(string mint)
        {
            ThreadPool.QueueUserWorkItem((state) =>
            {
                try
                {
                    string payload = string.Format("{{\"mint\": \"{0}\", \"reason\": \"MANUAL_UI_EXIT\"}}", EscapeJson(mint));
                    HttpPost(_baseUrl + "/api/bot/close", payload);
                    this.BeginInvoke(new Action(() =>
                    {
                        ShowToast("Orden de cierre manual enviada para " + mint);
                        AppendLog("[MANUAL CLOSE] Orden enviada para " + mint, Color.FromArgb(245, 158, 11));
                        PollAllData();
                    }));
                }
                catch (Exception ex)
                {
                    this.BeginInvoke(new Action(() =>
                    {
                        ShowToast("Error al cerrar posición: " + ex.Message);
                    }));
                }
            });
        }

        private void ToggleServer()
        {
            if (_btnToggleServer.Text.Contains("Iniciar"))
            {
                StartServerProcess();
            }
            else
            {
                StopServerProcess();
            }
        }

        private static string ResolvePythonExecutable(string baseDir)
        {
            string venvPython = Path.Combine(baseDir, ".venv", "Scripts", "python.exe");
            if (File.Exists(venvPython)) return venvPython;
            string venv2Python = Path.Combine(baseDir, "venv", "Scripts", "python.exe");
            if (File.Exists(venv2Python)) return venv2Python;
            return "python";
        }

        private void StartServerProcess()
        {
            try
            {
                string baseDir = AppDomain.CurrentDomain.BaseDirectory;
                ProcessStartInfo psi = new ProcessStartInfo();
                psi.FileName = ResolvePythonExecutable(baseDir);
                psi.Arguments = "-m pumpfun_detector.cockpit.server";
                psi.WorkingDirectory = baseDir;
                psi.UseShellExecute = false;
                psi.CreateNoWindow = true;
                psi.RedirectStandardOutput = true;
                psi.RedirectStandardError = true;

                _serverProcess = new Process();
                _serverProcess.StartInfo = psi;
                _serverProcess.OutputDataReceived += (s, e) =>
                {
                    if (!string.IsNullOrEmpty(e.Data))
                        AppendLog("[SERVER] " + e.Data, Color.FromArgb(148, 163, 184));
                };
                _serverProcess.ErrorDataReceived += (s, e) =>
                {
                    if (!string.IsNullOrEmpty(e.Data))
                        AppendLog("[SERVER ERR] " + e.Data, Color.FromArgb(245, 158, 11));
                };

                _serverProcess.Start();
                _serverProcess.BeginOutputReadLine();
                _serverProcess.BeginErrorReadLine();

                AppendLog("[SERVER] Proceso de trading cuantitativo Python iniciado (PID: " + _serverProcess.Id + ")", Color.FromArgb(0, 255, 170));
                _btnToggleServer.Text = "■ Detener Server";
                _btnToggleServer.BackColor = Color.FromArgb(218, 54, 51);
                Thread.Sleep(1000);
                CheckBackendHealth();
            }
            catch (Exception ex)
            {
                AppendLog("[ERROR] No se pudo iniciar el backend: " + ex.Message, Color.FromArgb(255, 51, 102));
            }
        }

        private void StopServerProcess()
        {
            try
            {
                if (_serverProcess != null && !_serverProcess.HasExited)
                {
                    _serverProcess.Kill();
                    _serverProcess = null;
                }
                AppendLog("[SERVER] Proceso backend detenido correctamente.", Color.FromArgb(245, 158, 11));
                _btnToggleServer.Text = "▶ Iniciar Server";
                _btnToggleServer.BackColor = Color.FromArgb(35, 134, 54);
                _lblStatus.Text = "○ Servidor detenido";
                _lblStatus.ForeColor = Color.FromArgb(245, 158, 11);
            }
            catch (Exception ex)
            {
                AppendLog("[ERROR] Al detener el servidor: " + ex.Message, Color.FromArgb(255, 51, 102));
            }
        }

        private void CleanShutdown()
        {
            try
            {
                if (_pollTimer != null)
                {
                    _pollTimer.Stop();
                    _pollTimer.Dispose();
                    _pollTimer = null;
                }
                if (_trayIcon != null)
                {
                    _trayIcon.Visible = false;
                    _trayIcon.Dispose();
                    _trayIcon = null;
                }
                if (_serverProcess != null && !_serverProcess.HasExited)
                {
                    _serverProcess.Kill();
                    _serverProcess = null;
                }
            }
            catch { }
        }

        private void SendAiPrompt()
        {
            string prompt = _txtPrompt.Text.Trim();
            if (string.IsNullOrEmpty(prompt)) return;

            AppendLog("USER > " + prompt, Color.FromArgb(88, 166, 255));
            _txtAiResponse.AppendText(string.Format("\r\n--- PROMPT [{0}] ---\r\n> {1}\r\nEsperando respuesta del modelo...\r\n", DateTime.Now.ToString("HH:mm:ss"), prompt));
            _txtPrompt.Clear();

            ThreadPool.QueueUserWorkItem((state) =>
            {
                try
                {
                    string payload = string.Format("{{\"message\": \"{0}\"}}", EscapeJson(prompt));
                    string response = HttpPost(_baseUrl + "/api/ai/chat", payload);
                    string reply = "";

                    try
                    {
                        Dictionary<string, object> resDict = _serializer.Deserialize<Dictionary<string, object>>(response);
                        reply = GetStr(resDict, "reply", response);
                    }
                    catch
                    {
                        reply = response;
                    }

                    this.BeginInvoke(new Action(() =>
                    {
                        AppendLog("AI > Respuesta recibida.", Color.FromArgb(168, 85, 247));
                        _txtAiResponse.AppendText("\r\nRESPUESTA:\r\n" + reply + "\r\n");
                        _txtAiResponse.ScrollToCaret();
                    }));
                }
                catch (Exception ex)
                {
                    this.BeginInvoke(new Action(() =>
                    {
                        AppendLog("[AI ERROR] " + ex.Message, Color.FromArgb(255, 51, 102));
                        _txtAiResponse.AppendText("\r\n[ERROR]: " + ex.Message + "\r\n");
                    }));
                }
            });
        }

        private void TriggerManualEvolution()
        {
            AppendLog("[EVOLVE] Ejecutando diagnóstico forense de trades y calibración adaptativa...", Color.FromArgb(0, 229, 255));
            ThreadPool.QueueUserWorkItem((state) =>
            {
                try
                {
                    string response = HttpPost(_baseUrl + "/api/evolution/run", "{}");
                    this.BeginInvoke(new Action(() =>
                    {
                        AppendLog("[EVOLVE COMPLETADO] " + response, Color.FromArgb(0, 255, 170));
                        PollAllData();
                    }));
                }
                catch (Exception ex)
                {
                    this.BeginInvoke(new Action(() =>
                    {
                        AppendLog("[EVOLVE ERROR] " + ex.Message, Color.FromArgb(255, 51, 102));
                    }));
                }
            });
        }

        private string HttpGet(string url)
        {
            HttpWebRequest req = (HttpWebRequest)WebRequest.Create(url);
            req.Timeout = 3000;
            req.Method = "GET";
            using (HttpWebResponse resp = (HttpWebResponse)req.GetResponse())
            using (StreamReader reader = new StreamReader(resp.GetResponseStream(), Encoding.UTF8))
            {
                return reader.ReadToEnd();
            }
        }

        private string HttpPost(string url, string jsonBody)
        {
            HttpWebRequest req = (HttpWebRequest)WebRequest.Create(url);
            req.Timeout = 20000;
            req.Method = "POST";
            req.ContentType = "application/json";
            byte[] bytes = Encoding.UTF8.GetBytes(jsonBody);
            req.ContentLength = bytes.Length;
            using (Stream os = req.GetRequestStream())
            {
                os.Write(bytes, 0, bytes.Length);
            }
            using (HttpWebResponse resp = (HttpWebResponse)req.GetResponse())
            using (StreamReader reader = new StreamReader(resp.GetResponseStream(), Encoding.UTF8))
            {
                return reader.ReadToEnd();
            }
        }

        private static string GetStr(Dictionary<string, object> dict, string key, string defVal)
        {
            if (dict == null || !dict.ContainsKey(key) || dict[key] == null) return defVal;
            return dict[key].ToString();
        }

        private static double GetNum(Dictionary<string, object> dict, string key, double defVal)
        {
            if (dict == null || !dict.ContainsKey(key) || dict[key] == null) return defVal;
            double d;
            if (double.TryParse(dict[key].ToString(), NumberStyles.Any, CultureInfo.InvariantCulture, out d))
                return d;
            return defVal;
        }

        private static Dictionary<string, object> GetDict(Dictionary<string, object> dict, string key)
        {
            if (dict == null || !dict.ContainsKey(key) || dict[key] == null) return null;
            return dict[key] as Dictionary<string, object>;
        }

        private static bool HasVal(Dictionary<string, object> dict, string key)
        {
            return dict != null && dict.ContainsKey(key) && dict[key] != null;
        }

        private static bool GetBool(Dictionary<string, object> dict, string key)
        {
            if (!HasVal(dict, key)) return false;
            object v = dict[key];
            if (v is bool) return (bool)v;
            bool b;
            if (bool.TryParse(v.ToString(), out b)) return b;
            return false;
        }

        private static ArrayList GetList(Dictionary<string, object> dict, string key)
        {
            if (dict == null || !dict.ContainsKey(key) || dict[key] == null) return null;
            object raw = dict[key];
            ArrayList al = raw as ArrayList;
            if (al != null) return al;
            IEnumerable seq = raw as IEnumerable;
            if (seq == null) return null;
            ArrayList outList = new ArrayList();
            foreach (object item in seq) outList.Add(item);
            return outList;
        }

        private static string EscapeJson(string s)
        {
            return s.Replace("\\", "\\\\").Replace("\"", "\\\"").Replace("\r", "").Replace("\n", "\\n");
        }
    }
}
