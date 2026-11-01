using System;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Reflection;
using System.Text;
using System.Threading;
using System.Windows.Forms;
using System.Runtime.InteropServices;
[assembly: AssemblyTitle("INWOUMEDIT")]
[assembly: AssemblyProduct("INWOUM EDIT")]
[assembly: AssemblyDescription("Open INWOUM EDIT desktop app")]
[assembly: AssemblyVersion("0.15.23.0")]
[assembly: AssemblyFileVersion("0.15.23.0")]
internal static class Launcher {
    [DllImport("shell32.dll", CharSet=CharSet.Unicode)]
    private static extern int SetCurrentProcessExplicitAppUserModelID(string id);
    [STAThread]
    private static int Main(string[] args) {
        string root = Path.GetDirectoryName(Assembly.GetExecutingAssembly().Location);
        string script = Path.Combine(root, "start.ps1");
        if (args.Length == 1 && args[0] == "--check") return File.Exists(script) ? 0 : 2;
        SetCurrentProcessExplicitAppUserModelID("INWOUM.EDIT.Desktop");
        Application.EnableVisualStyles();
        Application.SetCompatibleTextRenderingDefault(false);
        if (!File.Exists(script)) {
            MessageBox.Show("กรุณาวาง INWOUMEDIT.exe ไว้ในโฟลเดอร์โปรแกรมเดิม", "INWOUMEDIT", MessageBoxButtons.OK, MessageBoxIcon.Information);
            return 2;
        }
        using (Form form = new Form()) {
            form.Text = "INWOUMEDIT";
            form.Icon = Icon.ExtractAssociatedIcon(Assembly.GetExecutingAssembly().Location);
            form.ClientSize = new Size(390, 105);
            form.FormBorderStyle = FormBorderStyle.FixedDialog;
            form.StartPosition = FormStartPosition.CenterScreen;
            form.MaximizeBox = false; form.MinimizeBox = false; form.ShowInTaskbar = false;
            Label label = new Label(); label.AutoSize = false; label.Dock = DockStyle.Top;
            label.Height = 68; label.Padding = new Padding(16, 12, 12, 0);
            label.Font = new Font("Segoe UI", 10);
            label.Text = "กำลังเปิด INWOUM EDIT…\nครั้งแรกอาจต้องติดตั้งส่วนหน้าต่าง โปรดรอสักครู่";
            ProgressBar bar = new ProgressBar(); bar.Dock = DockStyle.Bottom;
            bar.Height = 8; bar.Style = ProgressBarStyle.Marquee;
            form.Controls.Add(label); form.Controls.Add(bar);
            form.Shown += delegate {
                Thread thread = new Thread(delegate() {
                    string error = null;
                    try {
                        ProcessStartInfo info = new ProcessStartInfo();
                        info.FileName = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.System), "WindowsPowerShell", "v1.0", "powershell.exe");
                        info.Arguments = "-NoProfile -ExecutionPolicy Bypass -File \"" + script + "\" -Desktop";
                        info.WorkingDirectory = root; info.UseShellExecute = false; info.CreateNoWindow = true;
                        info.RedirectStandardOutput = true; info.RedirectStandardError = true;
                        using (Process process = new Process()) {
                            process.StartInfo = info;
                            process.OutputDataReceived += delegate(object sender, DataReceivedEventArgs e) { };
                            process.ErrorDataReceived += delegate(object sender, DataReceivedEventArgs e) { };
                            process.Start(); process.BeginOutputReadLine(); process.BeginErrorReadLine(); process.WaitForExit();
                            if (process.ExitCode != 0) error = "เปิดโปรแกรมไม่สำเร็จ กรุณาเปิด START-APP.cmd เพื่อดูรายละเอียด";
                        }
                    } catch (Exception) { error = "เปิดโปรแกรมไม่สำเร็จ กรุณาลอง START-APP.cmd ในโฟลเดอร์โปรแกรม"; }
                    try {
                        if (!form.IsDisposed) form.BeginInvoke(new Action(delegate() {
                            if (error != null) MessageBox.Show(form, error, "INWOUMEDIT", MessageBoxButtons.OK, MessageBoxIcon.Information);
                            form.Close();
                        }));
                    } catch (InvalidOperationException) { }
                });
                thread.IsBackground = true; thread.Start();
            };
            Application.Run(form);
        }
        return 0;
    }
}
