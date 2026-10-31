; ModuleID = 'examples\m5.c'
source_filename = "examples\\m5.c"
target datalayout = "e-m:w-p270:32:32-p271:32:32-p272:64:64-i64:64-i128:128-f80:128-n8:16:32:64-S128"
target triple = "x86_64-pc-windows-msvc19.50.35723"

@sq = dso_local local_unnamed_addr global [64 x i32] zeroinitializer, align 16
@perm = dso_local local_unnamed_addr global [16 x i32] zeroinitializer, align 16
@big = dso_local local_unnamed_addr global [1000 x i32] zeroinitializer, align 16

; Function Attrs: nofree norecurse nosync nounwind memory(readwrite, argmem: none, inaccessiblemem: none) uwtable
define dso_local i32 @squares(i32 noundef %n) local_unnamed_addr #0 {
entry:
  br label %for.body

for.body:                                         ; preds = %for.body, %entry
  %indvars.iv = phi i64 [ 0, %entry ], [ %indvars.iv.next.3, %for.body ]
  %0 = mul nuw nsw i64 %indvars.iv, %indvars.iv
  %1 = trunc nuw i64 %0 to i32
  %add = add nsw i32 %n, %1
  %arrayidx = getelementptr inbounds nuw [64 x i32], ptr @sq, i64 0, i64 %indvars.iv
  store i32 %add, ptr %arrayidx, align 16
  %indvars.iv.next = or disjoint i64 %indvars.iv, 1
  %2 = mul nuw nsw i64 %indvars.iv.next, %indvars.iv.next
  %3 = trunc nuw i64 %2 to i32
  %add.1 = add nsw i32 %n, %3
  %arrayidx.1 = getelementptr inbounds nuw [64 x i32], ptr @sq, i64 0, i64 %indvars.iv.next
  store i32 %add.1, ptr %arrayidx.1, align 4
  %indvars.iv.next.1 = or disjoint i64 %indvars.iv, 2
  %4 = mul nuw nsw i64 %indvars.iv.next.1, %indvars.iv.next.1
  %5 = trunc nuw i64 %4 to i32
  %add.2 = add nsw i32 %n, %5
  %arrayidx.2 = getelementptr inbounds nuw [64 x i32], ptr @sq, i64 0, i64 %indvars.iv.next.1
  store i32 %add.2, ptr %arrayidx.2, align 8
  %indvars.iv.next.2 = or disjoint i64 %indvars.iv, 3
  %6 = mul nuw nsw i64 %indvars.iv.next.2, %indvars.iv.next.2
  %7 = trunc nuw i64 %6 to i32
  %add.3 = add nsw i32 %n, %7
  %arrayidx.3 = getelementptr inbounds nuw [64 x i32], ptr @sq, i64 0, i64 %indvars.iv.next.2
  store i32 %add.3, ptr %arrayidx.3, align 4
  %indvars.iv.next.3 = add nuw nsw i64 %indvars.iv, 4
  %exitcond.not.3 = icmp eq i64 %indvars.iv.next.3, 64
  br i1 %exitcond.not.3, label %for.body5, label %for.body, !llvm.loop !5

for.cond.cleanup4:                                ; preds = %for.body5
  ret i32 %add8.3

for.body5:                                        ; preds = %for.body, %for.body5
  %indvars.iv24 = phi i64 [ %indvars.iv.next25.3, %for.body5 ], [ 0, %for.body ]
  %s.020 = phi i32 [ %add8.3, %for.body5 ], [ 0, %for.body ]
  %arrayidx7 = getelementptr inbounds nuw [64 x i32], ptr @sq, i64 0, i64 %indvars.iv24
  %8 = load i32, ptr %arrayidx7, align 16
  %add8 = add nsw i32 %8, %s.020
  %indvars.iv.next25 = or disjoint i64 %indvars.iv24, 1
  %arrayidx7.1 = getelementptr inbounds nuw [64 x i32], ptr @sq, i64 0, i64 %indvars.iv.next25
  %9 = load i32, ptr %arrayidx7.1, align 4
  %add8.1 = add nsw i32 %9, %add8
  %indvars.iv.next25.1 = or disjoint i64 %indvars.iv24, 2
  %arrayidx7.2 = getelementptr inbounds nuw [64 x i32], ptr @sq, i64 0, i64 %indvars.iv.next25.1
  %10 = load i32, ptr %arrayidx7.2, align 8
  %add8.2 = add nsw i32 %10, %add8.1
  %indvars.iv.next25.2 = or disjoint i64 %indvars.iv24, 3
  %arrayidx7.3 = getelementptr inbounds nuw [64 x i32], ptr @sq, i64 0, i64 %indvars.iv.next25.2
  %11 = load i32, ptr %arrayidx7.3, align 4
  %add8.3 = add nsw i32 %11, %add8.2
  %indvars.iv.next25.3 = add nuw nsw i64 %indvars.iv24, 4
  %exitcond27.not.3 = icmp eq i64 %indvars.iv.next25.3, 64
  br i1 %exitcond27.not.3, label %for.cond.cleanup4, label %for.body5, !llvm.loop !8
}

; Function Attrs: mustprogress nocallback nofree nosync nounwind willreturn memory(argmem: readwrite)
declare void @llvm.lifetime.start.p0(i64 immarg, ptr nocapture) #1

; Function Attrs: mustprogress nocallback nofree nosync nounwind willreturn memory(argmem: readwrite)
declare void @llvm.lifetime.end.p0(i64 immarg, ptr nocapture) #1

; Function Attrs: nofree norecurse nosync nounwind memory(readwrite, argmem: none, inaccessiblemem: none) uwtable
define dso_local i32 @scatter(i32 noundef %k) local_unnamed_addr #0 {
entry:
  %inv = alloca [16 x i32], align 16
  br label %for.body

for.cond.cleanup:                                 ; preds = %for.body
  call void @llvm.lifetime.start.p0(i64 64, ptr nonnull %inv) #2
  br label %for.body5

for.body:                                         ; preds = %for.body, %entry
  %indvars.iv = phi i64 [ 0, %entry ], [ %indvars.iv.next.3, %for.body ]
  %0 = trunc i64 %indvars.iv to i32
  %1 = mul nuw nsw i32 %0, 5
  %2 = add i32 %1, %k
  %and = and i32 %2, 15
  %arrayidx = getelementptr inbounds nuw [16 x i32], ptr @perm, i64 0, i64 %indvars.iv
  store i32 %and, ptr %arrayidx, align 16
  %indvars.iv.next = or disjoint i64 %indvars.iv, 1
  %3 = trunc i64 %indvars.iv.next to i32
  %4 = mul nuw nsw i32 %3, 5
  %5 = add i32 %4, %k
  %and.1 = and i32 %5, 15
  %arrayidx.1 = getelementptr inbounds nuw [16 x i32], ptr @perm, i64 0, i64 %indvars.iv.next
  store i32 %and.1, ptr %arrayidx.1, align 4
  %indvars.iv.next.1 = or disjoint i64 %indvars.iv, 2
  %6 = trunc i64 %indvars.iv.next.1 to i32
  %7 = mul nuw nsw i32 %6, 5
  %8 = add i32 %7, %k
  %and.2 = and i32 %8, 15
  %arrayidx.2 = getelementptr inbounds nuw [16 x i32], ptr @perm, i64 0, i64 %indvars.iv.next.1
  store i32 %and.2, ptr %arrayidx.2, align 8
  %indvars.iv.next.2 = or disjoint i64 %indvars.iv, 3
  %9 = trunc i64 %indvars.iv.next.2 to i32
  %10 = mul nuw nsw i32 %9, 5
  %11 = add i32 %10, %k
  %and.3 = and i32 %11, 15
  %arrayidx.3 = getelementptr inbounds nuw [16 x i32], ptr @perm, i64 0, i64 %indvars.iv.next.2
  store i32 %and.3, ptr %arrayidx.3, align 4
  %indvars.iv.next.3 = add nuw nsw i64 %indvars.iv, 4
  %exitcond.not.3 = icmp eq i64 %indvars.iv.next.3, 16
  br i1 %exitcond.not.3, label %for.cond.cleanup, label %for.body, !llvm.loop !9

for.body5:                                        ; preds = %for.body5, %for.cond.cleanup
  %indvars.iv42 = phi i64 [ 0, %for.cond.cleanup ], [ %indvars.iv.next43.3, %for.body5 ]
  %arrayidx7 = getelementptr inbounds nuw [16 x i32], ptr @perm, i64 0, i64 %indvars.iv42
  %12 = load i32, ptr %arrayidx7, align 16
  %idxprom8 = sext i32 %12 to i64
  %arrayidx9 = getelementptr inbounds [16 x i32], ptr %inv, i64 0, i64 %idxprom8
  %13 = trunc nuw nsw i64 %indvars.iv42 to i32
  store i32 %13, ptr %arrayidx9, align 4
  %indvars.iv.next43 = or disjoint i64 %indvars.iv42, 1
  %arrayidx7.1 = getelementptr inbounds nuw [16 x i32], ptr @perm, i64 0, i64 %indvars.iv.next43
  %14 = load i32, ptr %arrayidx7.1, align 4
  %idxprom8.1 = sext i32 %14 to i64
  %arrayidx9.1 = getelementptr inbounds [16 x i32], ptr %inv, i64 0, i64 %idxprom8.1
  %15 = trunc nuw nsw i64 %indvars.iv.next43 to i32
  store i32 %15, ptr %arrayidx9.1, align 4
  %indvars.iv.next43.1 = or disjoint i64 %indvars.iv42, 2
  %arrayidx7.2 = getelementptr inbounds nuw [16 x i32], ptr @perm, i64 0, i64 %indvars.iv.next43.1
  %16 = load i32, ptr %arrayidx7.2, align 8
  %idxprom8.2 = sext i32 %16 to i64
  %arrayidx9.2 = getelementptr inbounds [16 x i32], ptr %inv, i64 0, i64 %idxprom8.2
  %17 = trunc nuw nsw i64 %indvars.iv.next43.1 to i32
  store i32 %17, ptr %arrayidx9.2, align 4
  %indvars.iv.next43.2 = or disjoint i64 %indvars.iv42, 3
  %arrayidx7.3 = getelementptr inbounds nuw [16 x i32], ptr @perm, i64 0, i64 %indvars.iv.next43.2
  %18 = load i32, ptr %arrayidx7.3, align 4
  %idxprom8.3 = sext i32 %18 to i64
  %arrayidx9.3 = getelementptr inbounds [16 x i32], ptr %inv, i64 0, i64 %idxprom8.3
  %19 = trunc nuw nsw i64 %indvars.iv.next43.2 to i32
  store i32 %19, ptr %arrayidx9.3, align 4
  %indvars.iv.next43.3 = add nuw nsw i64 %indvars.iv42, 4
  %exitcond45.not.3 = icmp eq i64 %indvars.iv.next43.3, 16
  br i1 %exitcond45.not.3, label %for.body17, label %for.body5, !llvm.loop !10

for.cond.cleanup16:                               ; preds = %for.body17
  call void @llvm.lifetime.end.p0(i64 64, ptr nonnull %inv) #2
  ret i32 %add21

for.body17:                                       ; preds = %for.body5, %for.body17
  %indvars.iv46 = phi i64 [ %indvars.iv.next47, %for.body17 ], [ 0, %for.body5 ]
  %s.037 = phi i32 [ %add21, %for.body17 ], [ 0, %for.body5 ]
  %arrayidx19 = getelementptr inbounds nuw [16 x i32], ptr %inv, i64 0, i64 %indvars.iv46
  %20 = load i32, ptr %arrayidx19, align 4
  %21 = trunc nuw nsw i64 %indvars.iv46 to i32
  %mul20 = mul nsw i32 %20, %21
  %add21 = add nsw i32 %mul20, %s.037
  %indvars.iv.next47 = add nuw nsw i64 %indvars.iv46, 1
  %exitcond49.not = icmp eq i64 %indvars.iv.next47, 16
  br i1 %exitcond49.not, label %for.cond.cleanup16, label %for.body17, !llvm.loop !11
}

; Function Attrs: nofree norecurse nosync nounwind memory(readwrite, argmem: none, inaccessiblemem: none) uwtable
define dso_local i32 @chunks(i32 noundef %n) local_unnamed_addr #0 {
entry:
  br label %for.body

for.cond.cleanup:                                 ; preds = %for.body
  %0 = load i32, ptr @big, align 16
  %1 = load i32, ptr getelementptr inbounds nuw (i8, ptr @big, i64 1596), align 4
  %mul = mul nsw i32 %1, 3
  %add2 = add nsw i32 %mul, %0
  %2 = load i32, ptr getelementptr inbounds nuw (i8, ptr @big, i64 3164), align 4
  %mul3 = mul nsw i32 %2, 5
  %add4 = add nsw i32 %add2, %mul3
  %3 = load i32, ptr getelementptr inbounds nuw (i8, ptr @big, i64 3976), align 8
  %mul5 = mul nsw i32 %3, 7
  %add6 = add nsw i32 %add4, %mul5
  ret i32 %add6

for.body:                                         ; preds = %for.body.3, %entry
  %indvars.iv = phi i64 [ 0, %entry ], [ %indvars.iv.next.3, %for.body.3 ]
  %arrayidx = getelementptr inbounds nuw [1000 x i32], ptr @big, i64 0, i64 %indvars.iv
  %4 = trunc i64 %indvars.iv to i32
  %5 = add i32 %n, %4
  store i32 %5, ptr %arrayidx, align 16
  %indvars.iv.next = add nuw nsw i64 %indvars.iv, 7
  %arrayidx.1 = getelementptr inbounds nuw [1000 x i32], ptr @big, i64 0, i64 %indvars.iv.next
  %6 = trunc i64 %indvars.iv.next to i32
  %7 = add i32 %n, %6
  store i32 %7, ptr %arrayidx.1, align 4
  %indvars.iv.next.1 = add nuw nsw i64 %indvars.iv, 14
  %arrayidx.2 = getelementptr inbounds nuw [1000 x i32], ptr @big, i64 0, i64 %indvars.iv.next.1
  %8 = trunc i64 %indvars.iv.next.1 to i32
  %9 = add i32 %n, %8
  store i32 %9, ptr %arrayidx.2, align 8
  %cmp.2 = icmp samesign ult i64 %indvars.iv, 979
  br i1 %cmp.2, label %for.body.3, label %for.cond.cleanup, !llvm.loop !12

for.body.3:                                       ; preds = %for.body
  %indvars.iv.next.2 = add nuw nsw i64 %indvars.iv, 21
  %arrayidx.3 = getelementptr inbounds nuw [1000 x i32], ptr @big, i64 0, i64 %indvars.iv.next.2
  %10 = trunc nuw i64 %indvars.iv.next.2 to i32
  %11 = add i32 %n, %10
  store i32 %11, ptr %arrayidx.3, align 4
  %indvars.iv.next.3 = add nuw nsw i64 %indvars.iv, 28
  br label %for.body, !llvm.loop !14
}

attributes #0 = { nofree norecurse nosync nounwind memory(readwrite, argmem: none, inaccessiblemem: none) uwtable "min-legal-vector-width"="0" "no-trapping-math"="true" "stack-protector-buffer-size"="8" "target-cpu"="x86-64" "target-features"="+cmov,+cx8,+fxsr,+mmx,+sse,+sse2,+x87" "tune-cpu"="generic" }
attributes #1 = { mustprogress nocallback nofree nosync nounwind willreturn memory(argmem: readwrite) }
attributes #2 = { nounwind }

!llvm.module.flags = !{!0, !1, !2, !3}
!llvm.ident = !{!4}

!0 = !{i32 1, !"wchar_size", i32 2}
!1 = !{i32 8, !"PIC Level", i32 2}
!2 = !{i32 7, !"uwtable", i32 2}
!3 = !{i32 1, !"MaxTLSAlign", i32 65536}
!4 = !{!"clang version 20.1.0"}
!5 = distinct !{!5, !6, !7}
!6 = !{!"llvm.loop.mustprogress"}
!7 = !{!"llvm.loop.unroll.disable"}
!8 = distinct !{!8, !6, !7}
!9 = distinct !{!9, !6, !7}
!10 = distinct !{!10, !6, !7}
!11 = distinct !{!11, !6, !7}
!12 = distinct !{!12, !6, !13}
!13 = !{!"llvm.loop.unroll.count", i32 4}
!14 = distinct !{!14, !7}

