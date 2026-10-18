package com.horizon.offline;

import org.junit.Test;
import static org.junit.Assert.*;
import java.io.*;
import java.nio.file.Files;
import java.util.*;
import java.util.concurrent.*;

public class QueueStoreTest {
    private File storage() throws IOException { return new File(Files.createTempDirectory("outbox-test").toFile(),"queue.bin"); }
    @Test public void idempotencyAndIllegalTransitionsSurviveRestart() throws Exception {
        File path=storage(); QueueStore q=new QueueStore(path);
        q.enqueue("tenant-a:1","payload"); q.enqueue("tenant-a:1","payload");
        try { q.enqueue("tenant-a:1","different"); fail(); } catch(IllegalArgumentException expected) { }
        try { q.ack("tenant-a:1"); fail(); } catch(IllegalStateException expected) { }
        assertEquals(1,q.count(QueueStore.Status.PENDING));
        assertEquals("tenant-a:1",q.acquire(0).id); q.ack("tenant-a:1"); q.ack("tenant-a:1");
        q=new QueueStore(path); assertEquals(1,q.count(QueueStore.Status.ACKED)); assertNull(q.acquire(Long.MAX_VALUE));
    }
    @Test public void boundedBackoffAndUnacknowledgedDeliveryRecovery() throws Exception {
        File path=storage(); QueueStore q=new QueueStore(path); q.enqueue("x","data");
        for(int attempt=1;attempt<=15;attempt++) {
            long now=10000000L*attempt;
            QueueStore.Entry e=q.acquire(now); assertNotNull(e); assertEquals(attempt,e.attempts);
            q.fail("x",now); long delay=1000L << Math.min(10,attempt-1);
            assertNull(q.acquire(now+delay-1));
            q=new QueueStore(path);
        }
        QueueStore.Entry e=q.acquire(Long.MAX_VALUE); assertNotNull(e);
        q=new QueueStore(path); assertEquals(QueueStore.Status.PENDING,q.get("x").status);
        assertEquals(e.attempts+1,q.acquire(0).attempts); q.ack("x");
    }
    @Test public void competingWorkersCannotAcquireOrAcknowledgeTwice() throws Exception {
        QueueStore q=new QueueStore(storage());
        ExecutorService pool=Executors.newFixedThreadPool(8);
        try {
            List<Callable<Void>> writers=new ArrayList<>();
            for(int t=0;t<8;t++) writers.add(() -> { for(int i=0;i<80;i++) q.enqueue("id:"+i,"value:"+i); return null; });
            for(Future<Void> f:pool.invokeAll(writers)) f.get();
            Set<String> acquired=ConcurrentHashMap.newKeySet(); List<Callable<Void>> workers=new ArrayList<>();
            for(int t=0;t<8;t++) workers.add(() -> {
                QueueStore.Entry e; while((e=q.acquire(0))!=null) { assertTrue(acquired.add(e.id)); q.ack(e.id); }
                return null;
            });
            for(Future<Void> f:pool.invokeAll(workers)) f.get();
            assertEquals(80,acquired.size()); assertEquals(80,q.count(QueueStore.Status.ACKED));
        } finally { pool.shutdownNow(); }
    }
    @Test public void seededFailureTracesPreserveAllOperationsAndPayloads() throws Exception {
        File path=storage(); QueueStore q=new QueueStore(path); Random random=new Random(761032);
        for(int i=0;i<60;i++) q.enqueue("id:"+i,"payload:"+i);
        long now=0;
        for(int step=0;step<1200;step++) {
            QueueStore.Entry e=q.acquire(now);
            if(e!=null) {
                assertEquals("payload:"+e.id.substring(3),e.payload);
                if(random.nextInt(4)==0) q.fail(e.id,now);
                else if(random.nextInt(5)==0) q=new QueueStore(path);
                else q.ack(e.id);
            }
            if(step%17==0) q=new QueueStore(path);
            assertEquals(60,q.count(QueueStore.Status.PENDING)+q.count(QueueStore.Status.INFLIGHT)+q.count(QueueStore.Status.ACKED));
            now+=2000000;
        }
        q=new QueueStore(path); QueueStore.Entry e;
        while((e=q.acquire(Long.MAX_VALUE))!=null) q.ack(e.id);
        assertEquals(60,q.count(QueueStore.Status.ACKED));
    }
    @Test public void corruptStorageFailsClosedAndFailedWriteDoesNotInventCommit() throws Exception {
        File path=storage(); QueueStore q=new QueueStore(path); q.enqueue("kept","original");
        File pending=new File(path.getParent(),path.getName()+".pending"); assertTrue(pending.mkdir());
        try { q.enqueue("lost","unsaved"); fail(); } catch(IOException expected) { }
        assertNull(q.get("lost")); assertEquals("original",new QueueStore(path).get("kept").payload);
        try(FileOutputStream out=new FileOutputStream(path)) { out.write(new byte[]{1,2,3}); }
        try { new QueueStore(path); fail(); } catch(IOException expected) { }
    }
}
