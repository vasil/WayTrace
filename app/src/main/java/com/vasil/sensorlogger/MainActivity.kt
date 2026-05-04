package com.vasil.sensorlogger

import android.Manifest
import android.content.Context
import android.content.pm.PackageManager
import android.hardware.Sensor
import android.hardware.SensorEvent
import android.hardware.SensorEventListener
import android.hardware.SensorManager
import android.os.Build
import android.os.Bundle
import android.os.Environment
import android.os.Handler
import android.os.Looper
import android.view.View
import android.view.WindowManager
import android.widget.Button
import android.widget.TextView
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import androidx.camera.core.CameraSelector
import androidx.camera.core.Preview
import androidx.camera.lifecycle.ProcessCameraProvider
import androidx.camera.video.FileOutputOptions
import androidx.camera.video.Quality
import androidx.camera.video.QualitySelector
import androidx.camera.video.Recorder
import androidx.camera.video.Recording
import androidx.camera.video.VideoCapture
import androidx.camera.view.PreviewView
import androidx.core.app.ActivityCompat
import androidx.core.content.ContextCompat
import java.io.BufferedWriter
import java.io.File
import java.io.FileWriter
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale
import kotlin.math.abs
import kotlin.math.sqrt

enum class RecordingState { READY, RECORDING, PAUSED, STOPPED }

class MainActivity : AppCompatActivity(), SensorEventListener {

    private lateinit var sensorManager: SensorManager
    private var accelerometer: Sensor? = null
    private var gyroscope: Sensor? = null

    private var state = RecordingState.READY
    private var writer: BufferedWriter? = null
    private var currentFile: File? = null

    private val INTERVAL_NS = 100_000_000L
    private var lastAccelTime = 0L
    private var lastGyroTime = 0L

    private val EVENT_COOLDOWN_NS = 500_000_000L
    private var lastBumpTime = 0L
    private var lastFallTime = 0L
    private var lastWheelieTime = 0L
    private var lastTiltTime = 0L

    private var bumpCount = 0
    private var maxMagnitude = 0f
    private var startTimeMs = 0L
    private var elapsedMs = 0L
    private var startDisplayTime = ""

    // CameraX
    private lateinit var previewView: PreviewView
    private var videoCapture: VideoCapture<Recorder>? = null
    private var activeRecording: Recording? = null

    private val timerHandler = Handler(Looper.getMainLooper())
    private val timerRunnable = object : Runnable {
        override fun run() {
            if (state == RecordingState.RECORDING) {
                elapsedMs = System.currentTimeMillis() - startTimeMs
                updateTimerDisplay()
                timerHandler.postDelayed(this, 1000)
            }
        }
    }

    private lateinit var tvAppName: TextView
    private lateinit var tvTimer: TextView
    private lateinit var btnPrimary: Button
    private lateinit var btnStop: Button
    private lateinit var tvStats: TextView

    companion object {
        private const val REQUEST_STORAGE = 1001
        private const val REQUEST_CAMERA = 1002
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
        setContentView(R.layout.activity_main)

        tvAppName = findViewById(R.id.tvAppName)
        tvTimer = findViewById(R.id.tvTimer)
        btnPrimary = findViewById(R.id.btnPrimary)
        btnStop = findViewById(R.id.btnStop)
        tvStats = findViewById(R.id.tvStats)
        previewView = findViewById(R.id.previewView)

        sensorManager = getSystemService(Context.SENSOR_SERVICE) as SensorManager
        accelerometer = sensorManager.getDefaultSensor(Sensor.TYPE_ACCELEROMETER)
        gyroscope = sensorManager.getDefaultSensor(Sensor.TYPE_GYROSCOPE)

        btnPrimary.setOnClickListener {
            when (state) {
                RecordingState.READY, RecordingState.STOPPED -> requestPermissionsAndStart()
                RecordingState.RECORDING -> pauseRecording()
                RecordingState.PAUSED -> resumeRecording()
            }
        }

        btnStop.setOnClickListener { stopRecording() }

        if (ContextCompat.checkSelfPermission(this, Manifest.permission.CAMERA)
            == PackageManager.PERMISSION_GRANTED) {
            setupCamera()
        }
        updateUI()
    }

    private fun setupCamera() {
        val cameraProviderFuture = ProcessCameraProvider.getInstance(this)
        cameraProviderFuture.addListener({
            val cameraProvider = cameraProviderFuture.get()

            val preview = Preview.Builder().build().also {
                it.setSurfaceProvider(previewView.surfaceProvider)
            }

            val recorder = Recorder.Builder()
                .setQualitySelector(QualitySelector.from(Quality.HD))
                .build()
            videoCapture = VideoCapture.withOutput(recorder)

            try {
                cameraProvider.unbindAll()
                cameraProvider.bindToLifecycle(
                    this,
                    CameraSelector.DEFAULT_BACK_CAMERA,
                    preview,
                    videoCapture
                )
            } catch (e: Exception) {
                Toast.makeText(this, "Camera setup failed: ${e.message}", Toast.LENGTH_LONG).show()
            }
        }, ContextCompat.getMainExecutor(this))
    }

    private fun requestPermissionsAndStart() {
        // Check storage permission (Android 9 and below only)
        if (Build.VERSION.SDK_INT <= Build.VERSION_CODES.P &&
            ContextCompat.checkSelfPermission(this, Manifest.permission.WRITE_EXTERNAL_STORAGE)
            != PackageManager.PERMISSION_GRANTED) {
            ActivityCompat.requestPermissions(
                this, arrayOf(Manifest.permission.WRITE_EXTERNAL_STORAGE), REQUEST_STORAGE
            )
            return
        }
        // Check camera permission
        if (ContextCompat.checkSelfPermission(this, Manifest.permission.CAMERA)
            != PackageManager.PERMISSION_GRANTED) {
            ActivityCompat.requestPermissions(
                this, arrayOf(Manifest.permission.CAMERA), REQUEST_CAMERA
            )
            return
        }
        startRecording()
    }

    override fun onRequestPermissionsResult(
        requestCode: Int, permissions: Array<out String>, grantResults: IntArray
    ) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults)
        when (requestCode) {
            REQUEST_STORAGE -> {
                if (grantResults.isNotEmpty() && grantResults[0] == PackageManager.PERMISSION_GRANTED) {
                    requestPermissionsAndStart()
                } else {
                    Toast.makeText(this, "Storage permission required", Toast.LENGTH_LONG).show()
                }
            }
            REQUEST_CAMERA -> {
                if (grantResults.isNotEmpty() && grantResults[0] == PackageManager.PERMISSION_GRANTED) {
                    setupCamera()
                    startRecording()
                } else {
                    Toast.makeText(this, "Camera permission required for video", Toast.LENGTH_LONG).show()
                }
            }
        }
    }

    private fun startRecording() {
        try {
            val now = Date()
            val timestamp = SimpleDateFormat("yyyyMMdd_HHmmss", Locale.getDefault()).format(now)
            startDisplayTime = SimpleDateFormat("HH:mm", Locale.getDefault()).format(now)

            val downloadsDir = Environment.getExternalStoragePublicDirectory(Environment.DIRECTORY_DOWNLOADS)
            downloadsDir.mkdirs()

            // Start CSV
            val csvFile = File(downloadsDir, "sensors_$timestamp.csv")
            currentFile = csvFile
            writer = BufferedWriter(FileWriter(csvFile))
            writer!!.write("timestamp_ms,sensor,x,y,z,event\n")

            // Start video
            val videoFile = File(downloadsDir, "sensors_$timestamp.mp4")
            val outputOptions = FileOutputOptions.Builder(videoFile).build()
            activeRecording = videoCapture?.output
                ?.prepareRecording(this, outputOptions)
                ?.start(ContextCompat.getMainExecutor(this)) {}

            bumpCount = 0
            maxMagnitude = 0f
            lastAccelTime = 0L
            lastGyroTime = 0L
            startTimeMs = System.currentTimeMillis()
            elapsedMs = 0L

            registerSensors()
            state = RecordingState.RECORDING
            updateUI()
            timerHandler.post(timerRunnable)
        } catch (e: Exception) {
            Toast.makeText(this, "Failed to start: ${e.message}", Toast.LENGTH_LONG).show()
        }
    }

    private fun pauseRecording() {
        sensorManager.unregisterListener(this)
        elapsedMs = System.currentTimeMillis() - startTimeMs
        timerHandler.removeCallbacks(timerRunnable)
        activeRecording?.pause()
        state = RecordingState.PAUSED
        updateUI()
    }

    private fun resumeRecording() {
        startTimeMs = System.currentTimeMillis() - elapsedMs
        registerSensors()
        activeRecording?.resume()
        state = RecordingState.RECORDING
        updateUI()
        timerHandler.post(timerRunnable)
    }

    private fun stopRecording() {
        sensorManager.unregisterListener(this)
        timerHandler.removeCallbacks(timerRunnable)
        if (state == RecordingState.RECORDING) {
            elapsedMs = System.currentTimeMillis() - startTimeMs
        }
        writer?.flush()
        writer?.close()
        writer = null
        activeRecording?.stop()
        activeRecording = null
        state = RecordingState.STOPPED
        updateUI()
    }

    private fun registerSensors() {
        sensorManager.registerListener(this, accelerometer, SensorManager.SENSOR_DELAY_FASTEST)
        sensorManager.registerListener(this, gyroscope, SensorManager.SENSOR_DELAY_FASTEST)
    }

    override fun onSensorChanged(event: SensorEvent) {
        if (state != RecordingState.RECORDING) return
        val nowNs = event.timestamp

        when (event.sensor.type) {
            Sensor.TYPE_ACCELEROMETER -> {
                if (lastAccelTime != 0L && nowNs - lastAccelTime < INTERVAL_NS) return
                lastAccelTime = nowNs
                val eventTag = detectAccelEvent(nowNs, event.values)
                if (eventTag == "bump") bumpCount++
                val mag = sqrt(
                    event.values[0] * event.values[0] +
                    event.values[1] * event.values[1] +
                    event.values[2] * event.values[2]
                )
                if (mag > maxMagnitude) maxMagnitude = mag
                writeLine(nowNs, "accel", event.values, eventTag)
            }
            Sensor.TYPE_GYROSCOPE -> {
                if (lastGyroTime != 0L && nowNs - lastGyroTime < INTERVAL_NS) return
                lastGyroTime = nowNs
                val eventTag = detectGyroEvent(nowNs, event.values)
                writeLine(nowNs, "gyro", event.values, eventTag)
            }
        }
    }

    private fun detectAccelEvent(nowNs: Long, v: FloatArray): String {
        val mag = sqrt(v[0] * v[0] + v[1] * v[1] + v[2] * v[2])
        if (mag > 12.0f && nowNs - lastBumpTime > EVENT_COOLDOWN_NS) {
            lastBumpTime = nowNs
            return "bump"
        }
        if (v[2] > 15.0f && nowNs - lastFallTime > EVENT_COOLDOWN_NS) {
            lastFallTime = nowNs
            return "fall"
        }
        return ""
    }

    private fun detectGyroEvent(nowNs: Long, v: FloatArray): String {
        if (abs(v[1]) > 3.0f && nowNs - lastWheelieTime > EVENT_COOLDOWN_NS) {
            lastWheelieTime = nowNs
            return "wheelie"
        }
        if (abs(v[0]) > 3.0f && nowNs - lastTiltTime > EVENT_COOLDOWN_NS) {
            lastTiltTime = nowNs
            return "tilt"
        }
        return ""
    }

    private fun writeLine(timestampNs: Long, sensor: String, values: FloatArray, event: String) {
        val ms = timestampNs / 1_000_000L
        val line = "$ms,$sensor,${values[0]},${values[1]},${values[2]},$event\n"
        try {
            writer?.write(line)
        } catch (e: Exception) {
            e.printStackTrace()
        }
    }

    private fun updateTimerDisplay() {
        val s = elapsedMs / 1000
        tvTimer.text = "%02d:%02d:%02d".format(s / 3600, (s % 3600) / 60, s % 60)
        tvStats.text = buildStatsLine()
    }

    private fun buildStatsLine(): String {
        val s = elapsedMs / 1000
        val duration = "%02d:%02d".format((s % 3600) / 60, s % 60)
        return "Started: $startDisplayTime | Duration: $duration | Bumps: $bumpCount | Max: ${"%.1f".format(maxMagnitude)} m/s²"
    }

    private fun updateUI() {
        when (state) {
            RecordingState.READY -> {
                tvTimer.visibility = View.GONE
                btnStop.visibility = View.GONE
                tvStats.visibility = View.GONE
                btnPrimary.text = "START"
                btnPrimary.setBackgroundColor(0xFF007700.toInt())
            }
            RecordingState.RECORDING -> {
                tvTimer.visibility = View.VISIBLE
                btnStop.visibility = View.VISIBLE
                tvStats.visibility = View.VISIBLE
                btnPrimary.text = "PAUSE"
                btnPrimary.setBackgroundColor(0xFFCC0000.toInt())
            }
            RecordingState.PAUSED -> {
                tvTimer.visibility = View.VISIBLE
                btnStop.visibility = View.VISIBLE
                tvStats.visibility = View.VISIBLE
                btnPrimary.text = "RESUME"
                btnPrimary.setBackgroundColor(0xFF007700.toInt())
            }
            RecordingState.STOPPED -> {
                tvTimer.visibility = View.GONE
                btnStop.visibility = View.GONE
                tvStats.visibility = View.VISIBLE
                tvStats.text = buildStatsLine()
                btnPrimary.text = "START"
                btnPrimary.setBackgroundColor(0xFF007700.toInt())
            }
        }
    }

    override fun onAccuracyChanged(sensor: Sensor?, accuracy: Int) {}

    override fun onDestroy() {
        super.onDestroy()
        if (state == RecordingState.RECORDING || state == RecordingState.PAUSED) stopRecording()
    }
}
