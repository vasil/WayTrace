package com.vasil.sensorlogger

import android.content.Context
import android.hardware.Sensor
import android.hardware.SensorEvent
import android.hardware.SensorEventListener
import android.hardware.SensorManager
import android.os.Bundle
import android.os.Environment
import android.widget.Button
import android.widget.TextView
import androidx.appcompat.app.AppCompatActivity
import java.io.BufferedWriter
import java.io.File
import java.io.FileWriter
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

class MainActivity : AppCompatActivity(), SensorEventListener {

    private lateinit var sensorManager: SensorManager
    private var accelerometer: Sensor? = null
    private var gyroscope: Sensor? = null

    private var isRecording = false
    private var writer: BufferedWriter? = null
    private var currentFile: File? = null

    // 10 Hz = one sample every 100ms
    private val INTERVAL_NS = 100_000_000L

    private var lastAccelTime = 0L
    private var lastGyroTime = 0L

    private lateinit var btnToggle: Button
    private lateinit var tvStatus: TextView

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)

        btnToggle = findViewById(R.id.btnToggle)
        tvStatus = findViewById(R.id.tvStatus)

        sensorManager = getSystemService(Context.SENSOR_SERVICE) as SensorManager
        accelerometer = sensorManager.getDefaultSensor(Sensor.TYPE_ACCELEROMETER)
        gyroscope = sensorManager.getDefaultSensor(Sensor.TYPE_GYROSCOPE)

        btnToggle.setOnClickListener {
            if (isRecording) stopRecording() else startRecording()
        }
    }

    private fun startRecording() {
        val timestamp = SimpleDateFormat("yyyyMMdd_HHmmss", Locale.getDefault()).format(Date())
        val fileName = "sensors_$timestamp.csv"

        val downloadsDir = Environment.getExternalStoragePublicDirectory(Environment.DIRECTORY_DOWNLOADS)
        downloadsDir.mkdirs()
        currentFile = File(downloadsDir, fileName)

        writer = BufferedWriter(FileWriter(currentFile!!))
        writer!!.write("timestamp_ms,sensor,x,y,z\n")

        lastAccelTime = 0L
        lastGyroTime = 0L

        sensorManager.registerListener(this, accelerometer, SensorManager.SENSOR_DELAY_FASTEST)
        sensorManager.registerListener(this, gyroscope, SensorManager.SENSOR_DELAY_FASTEST)

        isRecording = true
        btnToggle.text = "STOP"
        btnToggle.setBackgroundColor(0xFFCC0000.toInt())
        tvStatus.text = "Recording → $fileName"
    }

    private fun stopRecording() {
        sensorManager.unregisterListener(this)
        writer?.flush()
        writer?.close()
        writer = null

        isRecording = false
        btnToggle.text = "START"
        btnToggle.setBackgroundColor(0xFF007700.toInt())
        tvStatus.text = "Saved: ${currentFile?.name}\nLocation: Downloads/"
    }

    override fun onSensorChanged(event: SensorEvent) {
        val nowNs = event.timestamp

        when (event.sensor.type) {
            Sensor.TYPE_ACCELEROMETER -> {
                if (lastAccelTime != 0L && nowNs - lastAccelTime < INTERVAL_NS) return
                lastAccelTime = nowNs
                writeLine(nowNs, "accel", event.values)
            }
            Sensor.TYPE_GYROSCOPE -> {
                if (lastGyroTime != 0L && nowNs - lastGyroTime < INTERVAL_NS) return
                lastGyroTime = nowNs
                writeLine(nowNs, "gyro", event.values)
            }
        }
    }

    private fun writeLine(timestampNs: Long, sensor: String, values: FloatArray) {
        val ms = timestampNs / 1_000_000L
        val line = "$ms,$sensor,${values[0]},${values[1]},${values[2]}\n"
        try {
            writer?.write(line)
        } catch (e: Exception) {
            e.printStackTrace()
        }
    }

    override fun onAccuracyChanged(sensor: Sensor?, accuracy: Int) {}

    override fun onDestroy() {
        super.onDestroy()
        if (isRecording) stopRecording()
    }
}
