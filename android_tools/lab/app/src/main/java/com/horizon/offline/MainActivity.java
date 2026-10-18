package com.horizon.offline;

import android.app.Activity;
import android.os.Bundle;
import android.widget.Button;
import android.widget.LinearLayout;
import android.widget.TextView;
import java.io.File;
import java.io.IOException;
import java.util.UUID;

/** Native UI; all data is synthetic, and sync is explicitly a local simulation. */
public final class MainActivity extends Activity {
    private QueueStore queue;
    private TextView status;
    @Override public void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        LinearLayout layout = new LinearLayout(this);
        layout.setOrientation(LinearLayout.VERTICAL);
        int padding = (int) (24 * getResources().getDisplayMetrics().density);
        layout.setPadding(padding, padding, padding, padding);
        TextView description = new TextView(this);
        description.setText(R.string.description);
        layout.addView(description);
        status = new TextView(this);
        layout.addView(status);
        Button add = new Button(this);
        add.setText(R.string.enqueue);
        layout.addView(add);
        Button sync = new Button(this);
        sync.setText(R.string.sync);
        layout.addView(sync);
        setContentView(layout);
        try {
            queue = new QueueStore(new File(getFilesDir(), "outbox.bin"));
            render();
        } catch (IOException error) {
            status.setText(R.string.storage_error);
            add.setEnabled(false);
            sync.setEnabled(false);
            return;
        }
        add.setOnClickListener(view -> {
            try { queue.enqueue(UUID.randomUUID().toString(), getString(R.string.operation)); render(); }
            catch (IOException error) { status.setText(R.string.storage_error); }
        });
        sync.setOnClickListener(view -> {
            try {
                QueueStore.Entry entry = queue.acquire(System.currentTimeMillis());
                if (entry != null) queue.ack(entry.id);
                render();
            } catch (IOException error) { status.setText(R.string.storage_error); }
        });
    }
    private void render() {
        status.setText(getString(R.string.summary, queue.count(QueueStore.Status.PENDING),
            queue.count(QueueStore.Status.INFLIGHT), queue.count(QueueStore.Status.ACKED)));
    }
}
